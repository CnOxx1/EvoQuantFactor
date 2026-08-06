from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from factor_backend import __version__
from factor_backend.api.routes_batches import router as batches_router
from factor_backend.api.routes_factor_library import router as factor_library_router
from factor_backend.api.routes_jobs import router as jobs_router
from factor_backend.api.routes_llm import router as llm_router
from factor_backend.api.routes_prompts import router as prompts_router
from factor_backend.api.routes_reports import router as reports_router
from factor_backend.api.routes_system import router as system_router
from factor_backend.config import get_settings, validate_runtime_settings
from factor_backend.db.models import init_db
from factor_backend.services.news_summarize import start_news_summarize_workers, stop_news_summarize_workers
from factor_backend.services.report_ingest.collector import start_report_collector, stop_report_collector
from factor_backend.services.worker import start_worker, stop_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    validate_runtime_settings(settings)
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    if settings.worker_enabled:
        start_worker()
    start_news_summarize_workers()
    start_report_collector()
    yield
    stop_report_collector()
    stop_news_summarize_workers()
    stop_worker()


app = FastAPI(
    title="Factor Backend API",
    description="研报上传 → LangGraph 流水线 → 因子公式与逐步记录（含鉴权 / DB / LLM 配置）",
    version=__version__,
    lifespan=lifespan,
)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
# 浏览器不允许 credentials + Access-Control-Allow-Origin:*；通配时关闭 credentials
_wildcard_cors = origins == ["*"] or not origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _wildcard_cors else origins,
    allow_credentials=not _wildcard_cors,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router)
app.include_router(reports_router)
app.include_router(jobs_router)
app.include_router(batches_router)
app.include_router(llm_router)
app.include_router(prompts_router)
app.include_router(factor_library_router)


def run() -> None:
    s = get_settings()
    uvicorn.run("factor_backend.main:app", host=s.app_host, port=s.app_port, reload=False)


if __name__ == "__main__":
    run()
