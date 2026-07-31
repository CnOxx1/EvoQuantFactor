from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PromptSummary(BaseModel):
    key: str
    name: str
    source: str
    weights: dict[str, float] = Field(default_factory=dict)
    has_system: bool = False
    mcp_prefer_tools: list[str] = Field(default_factory=list)


class PromptConfigOut(BaseModel):
    key: str
    name: str
    system: str = ""
    user_template: str = ""
    weights: dict[str, float] = Field(default_factory=dict)
    scoring: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    source: str = "file"
    updated_at: str | None = None


class PromptConfigUpdate(BaseModel):
    name: str | None = None
    system: str | None = None
    user_template: str | None = None
    weights: dict[str, float] | None = None
    scoring: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None
    enabled: bool | None = True
