from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 默认只开东财源；其余适配器仍保留，需显式加入 REPORT_COLLECTOR_SOURCES
_DEFAULT_COLLECTOR_SOURCES = "eastmoney_report,eastmoney_news"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_env: str = "development"
    cors_origins: str = "*"

    # 鉴权：本地开发默认可关；生产启动校验会强制 AUTH_DISABLED=false + API_TOKEN
    auth_disabled: bool = True
    api_token: str = ""
    # 生产环境配置不当时是否拒绝启动（默认 true）
    strict_production: bool = True

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
    # Step2 六角色同时调用 LLM 的并发上限（降低限流/费用尖峰）
    review_concurrency: int = 3

    mcp_enabled: bool = False
    mcp_market_url: str = "http://127.0.0.1:8100/sse"

    data_dir: str = str(repo_root() / "data")
    prompts_dir: str = str(repo_root() / "prompts")
    # 参考文档路径；运行时不加载 YAML（门槛/模型以本 Settings + DB 为准）
    config_path: str = str(repo_root() / "config" / "default.yaml")

    worker_enabled: bool = True
    worker_poll_interval: float = 1.0
    worker_concurrency: int = 3
    job_timeout_sec: int = 1800
    job_failure_retries: int = 1

    # 采集默认关闭，避免拖垮 API 进程；需要时显式开启
    report_collector_enabled: bool = False
    report_collector_interval_sec: int = 600
    report_collector_sources: str = _DEFAULT_COLLECTOR_SOURCES
    report_collector_qtypes: str = "0,1,2,3"
    report_collector_news_columns: str = "350,344,355,354,351,353"
    report_collector_page_size: int = 20
    report_collector_lookback_hours: int = 24
    report_collector_request_gap_sec: float = 1.5

    luobo_cloud_sso_token: str = ""
    luobo_cookie: str = ""
    luobo_collect_feeds: bool = True
    luobo_collect_reports: bool = True

    # 摘要默认关闭；开启后默认低并发
    news_summarize_enabled: bool = False
    news_summarize_max_chars: int = 24000
    news_summarize_workers: int = 2
    news_summarize_queue_max: int = 500
    news_summarize_max_retries: int = 2
    news_summarize_workers_cap: int = 32

    report_collector_pdf_refetch_limit: int = 5
    report_collector_fingerprint_dedupe: bool = True
    report_collector_title_backfill_on_start: bool = True

    @property
    def use_mock_llm(self) -> bool:
        if self.llm_mock:
            return True
        return not bool(self.llm_api_key)

    def report_collector_qtype_list(self) -> list[int]:
        out: list[int] = []
        for part in (self.report_collector_qtypes or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                continue
        return out or [0, 1, 2, 3]

    def report_collector_source_list(self) -> list[str]:
        alias = {
            "eastmoney": "eastmoney_report",
            "datayes": "luobo",
            "robo": "luobo",
            "萝卜投研": "luobo",
            "wscn": "wallstreetcn",
            "华尔街见闻": "wallstreetcn",
            "sina_finance": "sina",
            "新浪": "sina",
            "10jqka": "ths",
            "同花顺": "ths",
            "金十": "jin10",
        }
        out: list[str] = []
        for part in (self.report_collector_sources or "").split(","):
            part = part.strip().lower()
            if not part:
                continue
            part = alias.get(part, part)
            out.append(part)
        return out or ["eastmoney_report", "eastmoney_news"]

    def luobo_configured(self) -> bool:
        return bool((self.luobo_cloud_sso_token or "").strip() or (self.luobo_cookie or "").strip())

    def is_production(self) -> bool:
        return (self.app_env or "").strip().lower() in {"production", "prod", "staging"}

    def security_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.auth_disabled:
            warnings.append("AUTH_DISABLED=true：API 未鉴权")
        elif not (self.api_token or "").strip():
            warnings.append("AUTH_DISABLED=false 但未设置 API_TOKEN")
        if (self.cors_origins or "").strip() == "*":
            warnings.append("CORS_ORIGINS=*：允许任意来源")
        if self.llm_mock and self.is_production():
            warnings.append("LLM_MOCK=true：生产环境默认走 mock（运行时仍以 DB 配置为准）")
        if self.mcp_enabled:
            warnings.append("MCP_ENABLED=true：行情 MCP 仍为 stub，评审证据勿当真实数据")
        if self.report_collector_enabled and self.worker_enabled:
            warnings.append("采集与 job worker 同进程：高负载时建议 --profile split 拆分")
        return warnings


def validate_runtime_settings(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    warnings = settings.security_warnings()
    for w in warnings:
        logger.warning("config: %s", w)

    if settings.is_production() and settings.strict_production:
        hard_errors: list[str] = []
        if settings.auth_disabled:
            hard_errors.append("生产环境禁止 AUTH_DISABLED=true（或设 STRICT_PRODUCTION=false 显式跳过）")
        if not (settings.api_token or "").strip():
            hard_errors.append("生产环境必须设置 API_TOKEN")
        if (settings.api_token or "").strip() in {"please-change-me", "changeme", "secret", "replace-with-a-long-random-token"}:
            hard_errors.append("生产环境 API_TOKEN 仍为占位值，请更换为强随机串")
        if hard_errors:
            msg = "; ".join(hard_errors)
            raise RuntimeError(f"不安全的生产配置: {msg}")
    return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
