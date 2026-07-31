from __future__ import annotations

from contextlib import asynccontextmanager

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
from factor_backend.config import get_settings
from factor_backend.db.models import init_db
from factor_backend.services.worker import start_worker, stop_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    Path = __import__("pathlib").Path
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    if settings.worker_enabled:
        start_worker()
    yield
    stop_worker()


app = FastAPI(
    title="Factor Backend API",
    description="研报上传 → LangGraph 流水线 → 因子公式与逐步记录（含鉴权 / DB / LLM 配置）",
    version=__version__,
    lifespan=lifespan,
)

settings = get_settings()
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
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
