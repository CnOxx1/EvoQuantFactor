from __future__ import annotations

from fastapi import APIRouter

from factor_backend import __version__
from factor_backend.config import get_settings
from factor_backend.services import metrics
from factor_backend.services.llm_config import llm_config_public_dict
from factor_backend.services.news_summarize import get_summarize_status
from factor_backend.services.prompt_loader import PromptLoader, ROLE_FILES
from factor_backend.services.report_ingest.collector import get_collector_status
from factor_backend.services.worker import worker_status

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    llm = {}
    try:
        llm = llm_config_public_dict()
    except Exception:  # noqa: BLE001
        llm = {"error": "llm_config_unavailable"}
    warnings = settings.security_warnings()
    workers = worker_status()
    status = "ok"
    if settings.is_production() and warnings:
        status = "degraded"
    return {
        "status": status,
        "version": __version__,
        "env": settings.app_env,
        "engine": "langgraph",
        "storage": "sqlalchemy",
        "auth_disabled": settings.auth_disabled,
        "worker_enabled": settings.worker_enabled,
        "worker": workers,
        "mcp_enabled": settings.mcp_enabled,
        "warnings": warnings,
        "llm": {
            "use_mock": llm.get("use_mock"),
            "should_call_llm": llm.get("should_call_llm"),
            "api_key_set": llm.get("api_key_set"),
            "model_step1": llm.get("model_step1"),
        },
    }


@router.get("/metrics")
def metrics_endpoint() -> dict:
    """进程内指标快照（JSON）；便于运维与调试，非 Prometheus 文本格式。"""
    settings = get_settings()
    out = metrics.snapshot()
    out["worker"] = worker_status()
    out["news_summarize"] = get_summarize_status()
    try:
        out["collector"] = get_collector_status()
    except Exception:  # noqa: BLE001
        out["collector"] = {"error": "unavailable"}
    out["config"] = {
        "env": settings.app_env,
        "worker_enabled": settings.worker_enabled,
        "report_collector_enabled": settings.report_collector_enabled,
        "news_summarize_enabled": settings.news_summarize_enabled,
        "review_concurrency": settings.review_concurrency,
        "mcp_enabled": settings.mcp_enabled,
    }
    return out


@router.get("/api/v1/meta")
def meta() -> dict:
    """元信息：健康检查级，默认不鉴权，方便前端启动页。"""
    settings = get_settings()
    loader = PromptLoader()
    return {
        "engine": "langgraph",
        "storage": "sqlalchemy",
        "auth_required": not settings.auth_disabled,
        "graph": [
            "ingest",
            "step1",
            "review_fanout",
            "review_merge",
            "code_gate",
            "persist",
        ],
        "save_rules": {
            "mean_min": settings.save_mean_min,
            "median_min": settings.save_median_min,
            "require_no_veto": True,
        },
        "max_round": settings.max_round,
        "roles": list(ROLE_FILES.keys()),
        "prompt_index": loader.index(),
        "llm": llm_config_public_dict(),
        "mcp_enabled": settings.mcp_enabled,
        "warnings": settings.security_warnings(),
        "worker": worker_status(),
        "endpoints": {
            "llm_config": "/api/v1/llm/config",
            "llm_test": "/api/v1/llm/test",
            "prompts": "/api/v1/prompts",
            "upload": "/api/v1/reports",
            "jobs": "/api/v1/jobs",
            "batches": "/api/v1/batches",
            "cancel": "/api/v1/jobs/{id}/cancel",
            "factor_library": "/api/v1/factor-library",
            "metrics": "/metrics",
        },
        "worker_concurrency": settings.worker_concurrency,
    }
