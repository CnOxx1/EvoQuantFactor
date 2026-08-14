from __future__ import annotations

import json
import re
from typing import Any

import httpx

from qfactor.settings import get_settings


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ):
        s = get_settings()
        self.api_key = api_key if api_key is not None else s.openai_api_key
        self.base_url = (base_url or s.openai_base_url).rstrip("/")
        self.model = model or s.openai_model
        self.reasoning_effort = (
            reasoning_effort if reasoning_effort is not None else s.openai_reasoning_effort
        ).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError(
                "生产因子需要配置 LLM Key：请在 .env 中设置 OPENAI_API_KEY "
                "（可选 OPENAI_BASE_URL / OPENAI_MODEL）后重试。"
            )

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        self.require_enabled()
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=120) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        return _extract_json(content)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("LLM response is not JSON")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM JSON root must be object")
    return data