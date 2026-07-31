from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ApiFormat = Literal["openai", "anthropic", "cursor"]


class LlmConfigUpdate(BaseModel):
    enabled: bool | None = None
    use_mock: bool | None = None
    api_format: ApiFormat | None = None
    base_url: str | None = None
    api_key: str | None = Field(
        default=None,
        description="新 Key；传空或不传表示不修改；传 *** 表示保持原样",
    )
    model_step1: str | None = None
    model_review: str | None = None
    timeout_sec: float | None = None
    max_retries: int | None = None


class LlmConfigOut(BaseModel):
    enabled: bool
    use_mock: bool
    api_format: ApiFormat
    base_url: str
    api_key_set: bool
    api_key_masked: str
    model_step1: str
    model_review: str
    timeout_sec: float
    max_retries: int
    should_call_llm: bool


class LlmTestOut(BaseModel):
    ok: bool
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
