from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_env: str = "development"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model_step1: str = "gpt-4o"
    llm_model_review: str = "gpt-4o-mini"

    save_mean_min: float = 80
    save_median_min: float = 75
    max_round: int = 3

    mcp_enabled: bool = False
    mcp_market_url: str = "http://market-mcp:8100/sse"

    redis_url: str = "redis://redis:6379/0"
    enable_redis: bool = False

    prompts_dir: str = str(_repo_root() / "prompts")
    config_path: str = str(_repo_root() / "config" / "default.yaml")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path or get_settings().config_path)
    if not cfg_path.exists():
        return {}
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_prompt_index() -> dict[str, Any]:
    prompts_dir = Path(os.getenv("PROMPTS_DIR") or get_settings().prompts_dir)
    index_path = prompts_dir / "index.json"
    if not index_path.exists():
        return {"agents": [], "error": f"missing {index_path}"}
    with index_path.open(encoding="utf-8") as f:
        return json.load(f)


def list_prompt_files() -> list[str]:
    prompts_dir = Path(os.getenv("PROMPTS_DIR") or get_settings().prompts_dir)
    if not prompts_dir.exists():
        return []
    return sorted(p.name for p in prompts_dir.glob("*.json"))
