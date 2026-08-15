from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from qfactor.agent.checkpoint import CheckpointStore
from qfactor.agent.loop import FactorLoop
from qfactor.data.dataset import DataService
from qfactor.factor.ops import LibraryOps
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import get_project_config, get_settings


def _templates() -> Jinja2Templates:
    root = get_project_config().root / "src" / "qfactor" / "web" / "templates"
    return Jinja2Templates(directory=str(root))


def _public_read_only_mode() -> bool:
    """Whether the externally exposed dashboard must deny all mutations."""
    return os.getenv("QFACTOR_READ_ONLY_WEB", "").strip().lower() in {"1", "true", "yes", "on"}


def _load_factory_monitor_status(cfg: Any) -> dict[str, Any]:
    """Read the atomic supervisor heartbeat without starting or controlling work."""
    status_path = Path(cfg.path("runs")) / "factory_monitor" / "status.json"
    if not status_path.exists():
        return {"state": "not_started", "status_path": str(status_path)}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"state": "unreadable", "error": str(exc)}
    return payload if isinstance(payload, dict) else {"state": "invalid_status"}


def create_app() -> FastAPI:
    app = FastAPI(title="qfactor", version="0.1.1")
    cfg = get_project_config()
    templates = _templates()
    jobs: dict[str, Any] = {"sync": None, "loop": None}
    read_only = _public_read_only_mode()

    def _require_mutation_allowed() -> None:
        if read_only:
            raise HTTPException(
                403,
                "公开监控站点为只读模式；数据同步、因子发现和库存操作仅可在云电脑本机执行。",
            )

    @app.middleware("http")
    async def enforce_public_read_only(request: Request, call_next: Any):
        if read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "公开监控站点为只读模式；所有写请求均已禁用。",
                },
            )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse("/ui/monitor" if read_only else "/ui/sync")

    @app.get("/ui/monitor", response_class=HTMLResponse)
    def ui_monitor(request: Request):
        monitor = _load_factory_monitor_status(cfg)
        counts = monitor.get("counts_after") or monitor.get("counts_before") or {}
        actions = monitor.get("actions") if isinstance(monitor.get("actions"), dict) else {}
        return templates.TemplateResponse(
            "monitor.html",
            {
                "request": request,
                "monitor": monitor,
                "counts": counts,
                "actions": actions,
                "read_only": read_only,
            },
        )

    @app.get("/ui/sync", response_class=HTMLResponse)
    def ui_sync(request: Request):
        status = DataService(cfg).status()
        return templates.TemplateResponse(
            "sync.html",
            {
                "request": request,
                "status": status,
                "job": jobs.get("sync"),
                "has_tushare": bool(get_settings().tushare_token),
            },
        )

    @app.post("/ui/sync")
    def ui_sync_post(
        background: BackgroundTasks,
        start: str = Form("20240101"),
        end: str = Form("20260630"),
        source: str = Form("baostock"),
        max_names: str = Form(""),
    ):
        _require_mutation_allowed()
        mn = int(max_names) if max_names.strip().isdigit() else None
        jobs["sync"] = {"state": "running", "start": start, "end": end, "source": source}

        def _run():
            try:
                meta = DataService(cfg).sync(
                    start, end, source=source, max_names=mn  # type: ignore[arg-type]
                )
                jobs["sync"] = {"state": "done", "meta": meta}
            except Exception as e:
                jobs["sync"] = {"state": "error", "error": str(e)}

        background.add_task(_run)
        return RedirectResponse("/ui/sync", status_code=303)

    @app.get("/ui/loop", response_class=HTMLResponse)
    def ui_loop(request: Request):
        cp = CheckpointStore("loop_csi100", cfg).load()
        return templates.TemplateResponse(
            "loop.html",
            {
                "request": request,
                "checkpoint": cp,
                "job": jobs.get("loop"),
                "has_openai": bool(get_settings().openai_api_key),
            },
        )

    @app.post("/ui/loop")
    def ui_loop_post(
        background: BackgroundTasks,
        rounds: int = Form(3),
        batch_size: int = Form(6),
        theme: str = Form(""),
        gate: str = Form("research"),
        llm_ratio: float = Form(0.45),
    ):
        _require_mutation_allowed()
        if gate != "research":
            raise HTTPException(
                400,
                "Mining loops are research-only; use library operations for production promotion.",
            )
        jobs["loop"] = {"state": "running", "rounds": rounds, "batch_size": batch_size}

        def _run():
            try:
                result = FactorLoop(cfg).run(
                    rounds=rounds,
                    batch_size=batch_size,
                    theme=theme or None,
                    gate_name=gate,
                    resume=True,
                    llm_ratio=llm_ratio,
                )
                jobs["loop"] = {"state": "done", "result": result}
            except Exception as e:
                jobs["loop"] = {"state": "error", "error": str(e)}

        background.add_task(_run)
        return RedirectResponse("/ui/loop", status_code=303)

    @app.get("/ui/factors", response_class=HTMLResponse)
    def ui_factors(request: Request, status: str = ""):
        factors = []
        try:
            from qfactor.db.repo import Database

            factors = Database().list_factors(status=status or None)
        except Exception:
            factors = []
        if not factors:
            factors = FactorRegistry(cfg).list_factors()
            if status:
                factors = [f for f in factors if f.get("status") == status]
        return templates.TemplateResponse(
            "factors.html",
            {"request": request, "factors": factors, "filter_status": status},
        )

    @app.get("/ui/factors/{name}", response_class=HTMLResponse)
    def ui_factor_detail(request: Request, name: str):
        reg = FactorRegistry(cfg)
        try:
            spec = reg.load_spec(name)
        except Exception as e:
            raise HTTPException(404, str(e)) from e
        report = None
        try:
            from qfactor.db.repo import Database

            report = Database().get_latest_report(name)
        except Exception:
            report = None
        if report is None:
            latest = reg.factor_dir(name) / "reports" / "latest.json"
            report = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None
        return templates.TemplateResponse(
            "factor_detail.html",
            {"request": request, "spec": spec.model_dump(), "report": report, "name": name},
        )

    # JSON API kept for automation
    @app.get("/api/db/status")
    def api_db_status() -> dict[str, Any]:
        from qfactor.db.models import db_path
        from qfactor.db.repo import Database

        status = Database().status()
        return status if read_only else {"path": str(db_path(cfg)), **status}

    @app.get("/api/factory/status")
    def api_factory_status() -> dict[str, Any]:
        return _load_factory_monitor_status(cfg)

    @app.get("/api/factors")
    def api_factors(status: str = "") -> list[dict[str, Any]]:
        try:
            from qfactor.db.repo import Database

            rows = Database().list_factors(status=status or None)
            if rows:
                return rows
        except Exception:
            pass
        factors = FactorRegistry(cfg).list_factors()
        if status:
            factors = [f for f in factors if f.get("status") == status]
        return factors

    @app.get("/api/data/status")
    def api_data_status() -> dict[str, Any]:
        return DataService(cfg).status()

    @app.get("/api/loop/status")
    def api_loop_status() -> dict[str, Any]:
        return {
            "checkpoint": CheckpointStore("loop_csi100", cfg).load(),
            "job": jobs.get("loop"),
        }

    class SyncBody(BaseModel):
        start: str
        end: str
        source: Literal["tushare", "akshare", "baostock", "auto"] = "auto"
        max_names: int | None = None

    @app.post("/api/data/sync")
    def api_sync(body: SyncBody) -> dict[str, Any]:
        _require_mutation_allowed()
        return DataService(cfg).sync(
            body.start, body.end, source=body.source, max_names=body.max_names
        )

    class LoopBody(BaseModel):
        rounds: int = 5
        batch_size: int = 8
        theme: str | None = None
        gate_name: Literal["research"] = "research"
        resume: bool = True
        llm_ratio: float = 0.45
        llm_review_ratio: float = 0.0

    @app.post("/api/agent/loop")
    def api_loop(body: LoopBody) -> dict[str, Any]:
        _require_mutation_allowed()
        try:
            return FactorLoop(cfg).run(
                rounds=body.rounds,
                batch_size=body.batch_size,
                theme=body.theme,
                gate_name=body.gate_name,
                resume=body.resume,
                llm_ratio=body.llm_ratio,
                llm_review_ratio=body.llm_review_ratio,
            )
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/library/archive")
    def api_archive() -> dict[str, Any]:
        _require_mutation_allowed()
        return LibraryOps(cfg).archive_stale()

    @app.post("/api/library/demote-corr")
    def api_demote_corr() -> dict[str, Any]:
        _require_mutation_allowed()
        return LibraryOps(cfg).demote_high_corr()

    return app


app = create_app()
