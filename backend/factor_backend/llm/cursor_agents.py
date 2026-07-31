from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from factor_backend.llm.errors import LlmError

DEFAULT_CURSOR_BASE = "https://api.cursor.com"
TERMINAL_STATUSES = frozenset({"FINISHED", "ERROR", "CANCELLED", "EXPIRED"})
POLL_INTERVAL_SEC = 2.0


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _normalize_base(base_url: str) -> str:
    base = (base_url or DEFAULT_CURSOR_BASE).rstrip("/")
    # 允许用户填 https://api.cursor.com 或带 /v1
    if base.endswith("/v1"):
        return base[: -len("/v1")]
    return base


async def cursor_run_prompt(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt_text: str,
    timeout_sec: float,
    name: str | None = None,
) -> dict[str, Any]:
    """
    调用 Cursor Cloud Agents API（无仓库 agent）：
    POST /v1/agents → 轮询 GET /v1/agents/{id}/runs/{runId} → 取 result 文本。
    """
    if not api_key:
        raise LlmError("未配置 Cursor API Key")

    root = _normalize_base(base_url)
    headers = _auth_headers(api_key)
    create_url = f"{root}/v1/agents"
    body: dict[str, Any] = {
        "prompt": {"text": prompt_text},
        "name": (name or "factor-llm")[:100],
    }
    if model and model.strip() and model.strip().lower() not in ("auto", "default"):
        body["model"] = {"id": model.strip()}

    timeout = httpx.Timeout(timeout_sec)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(create_url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise LlmError(f"Cursor 创建 Agent HTTP {resp.status_code}: {resp.text[:500]}")
        created = resp.json()
        agent = created.get("agent") or {}
        run = created.get("run") or {}
        agent_id = agent.get("id")
        run_id = run.get("id") or agent.get("latestRunId")
        if not agent_id or not run_id:
            raise LlmError(f"Cursor 创建 Agent 响应缺少 id: {str(created)[:400]}")

        deadline = time.monotonic() + max(10.0, float(timeout_sec))
        run_url = f"{root}/v1/agents/{agent_id}/runs/{run_id}"
        last: dict[str, Any] = run

        while True:
            status = str(last.get("status") or "").upper()
            if status in TERMINAL_STATUSES:
                break
            if time.monotonic() >= deadline:
                raise LlmError(
                    f"Cursor Agent 超时（>{timeout_sec}s） agent={agent_id} run={run_id} status={status or 'unknown'}"
                )
            await asyncio.sleep(POLL_INTERVAL_SEC)
            poll = await client.get(run_url, headers=headers)
            if poll.status_code >= 400:
                raise LlmError(f"Cursor 轮询 Run HTTP {poll.status_code}: {poll.text[:500]}")
            last = poll.json()

        status = str(last.get("status") or "").upper()
        result_text = last.get("result")
        if status != "FINISHED":
            raise LlmError(
                f"Cursor Run 失败 status={status} agent={agent_id} run={run_id} detail={str(last)[:400]}"
            )
        if not isinstance(result_text, str) or not result_text.strip():
            raise LlmError(f"Cursor Run 无 result 文本 agent={agent_id} run={run_id}")

        # 尽量清理，避免堆积无仓库 agent
        try:
            await client.delete(f"{root}/v1/agents/{agent_id}", headers=headers)
        except Exception:  # noqa: BLE001
            pass

        return {
            "text": result_text.strip(),
            "agent_id": agent_id,
            "run_id": run_id,
            "duration_ms": last.get("durationMs"),
            "url": f"{root}/v1/agents",
        }


def build_cursor_json_prompt(*, system: str, user: str) -> str:
    return (
        "You are a JSON-only API. Do not use tools, do not edit files, do not ask questions.\n"
        "Reply with a single JSON object only — no markdown fences, no commentary.\n\n"
        f"## System\n{system}\n\n"
        f"## User\n{user}\n"
    )


def build_cursor_ping_prompt() -> str:
    return (
        "You are a connectivity probe. Do not use tools or edit files. "
        "Reply with exactly one word: pong"
    )
