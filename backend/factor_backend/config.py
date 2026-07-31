from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_env: str = "development"
    cors_origins: str = "*"

    # 鉴权：生产请 AUTH_DISABLED=false 并设置 API_TOKEN
    auth_disabled: bool = True
    api_token: str = ""

    # 存储：sqlite 默认；生产可改为 postgresql+psycopg://...
    database_url: str = f"sqlite:///{(repo_root() / 'data' / 'factor.db').as_posix()}"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model_step1: str = "gpt-4o"
    llm_model_review: str = "gpt-4o-mini"
    llm_mock: bool = True  # 仅作 DB 初始默认；运行时以 /api/v1/llm/config 为准

    save_mean_min: float = 80
    save_median_min: float = 75
    max_round: int = 3

    mcp_enabled: bool = False
    mcp_market_url: str = "http://market-mcp:8100/sse"

    data_dir: str = str(repo_root() / "data")
    prompts_dir: str = str(repo_root() / "prompts")
    config_path: str = str(repo_root() / "config" / "default.yaml")

    worker_enabled: bool = True
    worker_poll_interval: float = 1.0
    worker_concurrency: int = 3  # 同时处理多份研报的并行 worker 数
    job_timeout_sec: int = 1800  # 单任务默认 30 分钟
    job_failure_retries: int = 1  # 整图失败后再重试次数（不含首次）

    @property
    def use_mock_llm(self) -> bool:
        if self.llm_mock:
            return True
        return not bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
