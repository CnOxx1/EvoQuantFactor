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

    # 研报/资讯自动采集（半自动：入库 + 资讯摘要，不自动建因子 job）
    report_collector_enabled: bool = True
    report_collector_interval_sec: int = 600
    # 多源（逗号分隔）：eastmoney_report,eastmoney_news,luobo,wallstreetcn,sina,ths,jin10
    report_collector_sources: str = (
        "eastmoney_report,eastmoney_news,wallstreetcn,sina,ths,jin10,luobo"
    )
    report_collector_qtypes: str = "0,1,2,3"  # 个股/行业/策略/宏观
    report_collector_news_columns: str = "350,344,355,354,351,353"  # 导读/股市/公司/宏观/产经/国际
    report_collector_page_size: int = 20
    report_collector_lookback_hours: int = 24
    report_collector_request_gap_sec: float = 1.5

    # 萝卜投研登录态（浏览器登录 robo.datayes.com 后从 Cookie 复制）
    luobo_cloud_sso_token: str = ""
    luobo_cookie: str = ""
    luobo_collect_feeds: bool = True
    luobo_collect_reports: bool = True

    # 资讯入库后自动 LLM 摘要（非因子流水线；多 worker = 多路并行 LLM/Cursor agent）
    news_summarize_enabled: bool = True
    news_summarize_max_chars: int = 24000
    news_summarize_workers: int = 8
    news_summarize_queue_max: int = 500
    news_summarize_max_retries: int = 2
    news_summarize_workers_cap: int = 32  # 安全上限，防止误配过大

    # 采集优化：每轮自动重抓 incomplete PDF 条数；跨源指纹去重
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
        return out or [
            "eastmoney_report",
            "eastmoney_news",
            "wallstreetcn",
            "sina",
            "ths",
            "jin10",
            "luobo",
        ]

    def luobo_configured(self) -> bool:
        return bool((self.luobo_cloud_sso_token or "").strip() or (self.luobo_cookie or "").strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
