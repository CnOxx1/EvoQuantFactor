from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from factor_backend.llm.cursor_agents import (
    build_cursor_json_prompt,
    build_cursor_ping_prompt,
    cursor_run_prompt,
)
from factor_backend.llm.errors import LlmError
from factor_backend.llm.json_extract import extract_json
from factor_backend.services.llm_config import LlmRuntimeConfig, get_llm_config

ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 8192

__all__ = ["LlmClient", "LlmError"]


def _extract_json(text: str) -> Any:
    return extract_json(text)


def _anthropic_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


class LlmClient:
    def __init__(self, cfg: LlmRuntimeConfig | None = None) -> None:
        self.cfg = cfg or get_llm_config()

    def _build_request(
        self,
        *,
        system: str,
        user: str,
        model: str | None,
        temperature: float,
        require_json_object: bool = True,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        api_format = (self.cfg.api_format or "openai").lower()
        model_name = model or self.cfg.model_step1
        base = self.cfg.base_url.rstrip("/")
        token_limit = max_tokens if max_tokens is not None else ANTHROPIC_MAX_TOKENS

        if api_format == "anthropic":
            url = f"{base}/messages"
            headers = {
                "x-api-key": self.cfg.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            }
            payload: dict[str, Any] = {
                "model": model_name,
                "max_tokens": token_limit,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
            return url, headers, payload

        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if require_json_object:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        elif not require_json_object:
            payload["max_tokens"] = token_limit
        return url, headers, payload

    def _parse_response(self, data: dict[str, Any]) -> Any:
        api_format = (self.cfg.api_format or "openai").lower()
        if api_format == "anthropic":
            content = _anthropic_text(data)
        else:
            content = data["choices"][0]["message"]["content"]
        try:
            return _extract_json(content)
        except json.JSONDecodeError as e:
            raise LlmError(f"模型返回非合法 JSON: {e}") from e

    def _raw_text(self, data: dict[str, Any]) -> str:
        api_format = (self.cfg.api_format or "openai").lower()
        if api_format == "anthropic":
            return _anthropic_text(data)
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            return json.dumps(data, ensure_ascii=False)[:500]

    async def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        last_err: Exception | None = None
        # 硬超时：流式慢吐字时 httpx read timeout 会被不断刷新，必须限制整次请求墙钟时间
        timeout = httpx.Timeout(
            connect=min(30.0, float(self.cfg.timeout_sec)),
            read=float(self.cfg.timeout_sec),
            write=min(60.0, float(self.cfg.timeout_sec)),
            pool=30.0,
        )
        for attempt in range(self.cfg.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    resp = await asyncio.wait_for(
                        client.post(url, headers=headers, json=payload),
                        timeout=float(self.cfg.timeout_sec) + 5.0,
                    )
                    if resp.status_code >= 400:
                        raise LlmError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
                    return resp.json()
            except asyncio.TimeoutError:
                last_err = LlmError(f"LLM 调用超时（>{self.cfg.timeout_sec}s）")
                # 超时不重试：避免 3×timeout 看起来像永久卡死
                break
            except LlmError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt >= self.cfg.max_retries:
                    break
        raise LlmError(f"LLM 调用失败: {last_err}")

    async def _cursor_text(self, *, prompt_text: str, model: str | None, name: str) -> dict[str, Any]:
        return await cursor_run_prompt(
            api_key=self.cfg.api_key,
            base_url=self.cfg.base_url or "https://api.cursor.com",
            model=model or self.cfg.model_step1,
            prompt_text=prompt_text,
            timeout_sec=self.cfg.timeout_sec,
            name=name,
        )

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> Any:
        if not self.cfg.api_key:
            raise LlmError("未配置 LLM API Key，请先在前端/接口写入 LLM 配置")
        if self.cfg.use_mock:
            raise LlmError("当前 LLM 配置为 use_mock=true，不会调用真实模型")

        api_format = (self.cfg.api_format or "openai").lower()
        if api_format == "cursor":
            meta = await self._cursor_text(
                prompt_text=build_cursor_json_prompt(system=system, user=user),
                model=model,
                name="factor-chat-json",
            )
            try:
                return _extract_json(meta["text"])
            except json.JSONDecodeError as e:
                raise LlmError(f"Cursor 返回非合法 JSON: {e}") from e

        last_err: Exception | None = None
        for require_json in (True, False):
            try:
                sys_prompt = system
                if not require_json:
                    sys_prompt += "\n输出必须是单个合法 JSON 对象，不要 markdown，不要尾逗号。"
                url, headers, payload = self._build_request(
                    system=sys_prompt,
                    user=user,
                    model=model,
                    temperature=temperature,
                    require_json_object=require_json,
                    max_tokens=ANTHROPIC_MAX_TOKENS,
                )
                data = await self._post(url, headers, payload)
                return self._parse_response(data)
            except LlmError as e:
                last_err = e
                msg = str(e)
                if require_json and ("HTTP 400" in msg or "非合法 JSON" in msg):
                    continue
                raise
        raise LlmError(f"LLM JSON 解析失败: {last_err}")

    async def test_connection(self) -> dict[str, Any]:
        """轻量连通探测。"""
        if not self.cfg.api_key:
            raise LlmError("未配置 LLM API Key，请先在前端/接口写入 LLM 配置")
        if self.cfg.use_mock:
            raise LlmError("当前 LLM 配置为 use_mock=true，不会调用真实模型")

        api_format = (self.cfg.api_format or "openai").lower()
        if api_format == "cursor":
            meta = await self._cursor_text(
                prompt_text=build_cursor_ping_prompt(),
                model=self.cfg.model_review or self.cfg.model_step1,
                name="factor-llm-ping",
            )
            text = meta["text"]
            return {
                "ok": True,
                "sample": text[:200],
                "api_format": "cursor",
                "model": self.cfg.model_review or self.cfg.model_step1,
                "agent_id": meta.get("agent_id"),
                "run_id": meta.get("run_id"),
                "url": meta.get("url"),
            }

        url, headers, payload = self._build_request(
            system="You are a connectivity probe. Reply with the single word: pong",
            user="ping",
            model=self.cfg.model_review or self.cfg.model_step1,
            temperature=0,
            require_json_object=False,
            max_tokens=64,
        )
        data = await self._post(url, headers, payload)
        text = self._raw_text(data).strip()
        if not text:
            raise LlmError("模型返回空内容")
        return {
            "ok": True,
            "sample": text[:200],
            "api_format": self.cfg.api_format,
            "model": payload.get("model"),
            "url": url,
        }
