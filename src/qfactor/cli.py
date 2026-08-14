from __future__ import annotations

import json
from typing import Optional

import typer
from rich import print

from qfactor.agent.loop import FactorLoop, FactorMiningAgent
from qfactor.data.dataset import DataService
from qfactor.eval.service import EvalService
from qfactor.factor.ops import LibraryOps
from qfactor.factor.registry import FactorRegistry
from qfactor.seeds import install_seed_factors
from qfactor.settings import get_project_config

app = typer.Typer(help="qfactor: CSI100 price-volume factor library + LLM mining")


@app.command("sync-data")
def sync_data(
    start: str = typer.Option(..., help="YYYYMMDD"),
    end: str = typer.Option(..., help="YYYYMMDD"),
    source: str = typer.Option("auto", help="tushare|baostock|akshare|auto"),
    max_names: Optional[int] = typer.Option(None, help="limit names for smoke test"),
):
    meta = DataService().sync(start, end, source=source, max_names=max_names)  # type: ignore[arg-type]
    print(meta)


@app.command("sync-universe")
def sync_universe(
    start: str = typer.Option(..., help="YYYYMMDD"),
    end: str = typer.Option(..., help="YYYYMMDD"),
):
    """Refresh point-in-time CSI100 members. Requires TUSHARE_TOKEN. Does not re-download bars."""
    print(DataService().sync_universe(start, end))


@app.command("install-seeds")
def install_seeds():
    paths = install_seed_factors()
    print({"installed": paths})


@app.command("list-factors")
def list_factors():
    print(FactorRegistry().list_factors())


@app.command("eval-factor")
def eval_factor(
    name: str,
    gate: str = typer.Option("research", "--gate"),
):
    report = EvalService().evaluate_and_save(name, gate_name=gate)
    print(report["summary"])
    print({"status": report["gate"]["status"], "checks": report["gate"]["checks"]})


@app.command("promote")
def promote(name: str, status: str = typer.Option("approved")):
    FactorRegistry().update_status(name, status)
    print({"name": name, "status": status})


@app.command("mine")
def mine(
    theme: str = typer.Option(..., help="e.g. reversal / liquidity / volatility"),
    max_iters: int = typer.Option(3),
    gate: str = typer.Option("research"),
    llm_ratio: float = typer.Option(0.45, help="share of candidates from LLM"),
    llm_review_ratio: float = typer.Option(0.0, help="share of candidates LLM-reviewed (advisory)"),
):
    """Requires OPENAI_API_KEY."""
    result = FactorMiningAgent().mine(
        theme=theme,
        max_iters=max_iters,
        gate_name=gate,
        llm_ratio=llm_ratio,
        llm_review_ratio=llm_review_ratio,
    )
    print(
        {
            "factor": result.get("factor"),
            "status": result.get("status"),
            "mode": result.get("mode"),
            "produced": len(result.get("produced", [])),
            "llm_ratio": result.get("llm_ratio"),
            "run_dir": result.get("run_dir"),
        }
    )


@app.command("loop")
def loop(
    rounds: int = typer.Option(5, help="loop rounds"),
    batch_size: int = typer.Option(8, help="candidates per round"),
    theme: Optional[str] = typer.Option(None, help="optional mechanism theme"),
    gate: str = typer.Option("research"),
    resume: bool = typer.Option(True, help="resume checkpoint"),
    llm_ratio: float = typer.Option(0.45, help="share of candidates from LLM"),
    llm_review_ratio: float = typer.Option(0.0, help="share of candidates LLM-reviewed (advisory)"),
):
    """Generate -> review -> validate. Requires OPENAI_API_KEY."""
    result = FactorLoop().run(
        rounds=rounds,
        batch_size=batch_size,
        theme=theme,
        gate_name=gate,
        resume=resume,
        llm_ratio=llm_ratio,
        llm_review_ratio=llm_review_ratio,
    )
    print(
        {
            "produced": result.get("produced"),
            "saved_total": result.get("saved_total"),
            "production_promo": result.get("production_promo"),
            "status": result.get("status"),
            "mechanism_hits": result.get("mechanism_hits"),
            "checkpoint": result.get("checkpoint"),
            "run_dir": result.get("run_dir"),
            "mode": result.get("mode"),
            "orchestrator": result.get("orchestrator"),
            "llm_ratio": result.get("llm_ratio"),
        }
    )


@app.command("library-archive")
def library_archive(force_rejects: bool = typer.Option(False, help="archive reject drafts now")):
    print(LibraryOps().archive_stale(force_rejects=force_rejects))


@app.command("library-demote-corr")
def library_demote_corr(max_corr: float = typer.Option(0.70)):
    print(LibraryOps().demote_high_corr(max_corr=max_corr))


@app.command("library-cap-usable")
def library_cap_usable(max_per: int = typer.Option(1, help="max candidate/approved per mechanism")):
    print(LibraryOps().cap_usable_per_mechanism(max_per=max_per))


@app.command("library-reeval-screened")
def library_reeval_screened():
    """Run production gate on screened factors; passers become candidate."""
    print(LibraryOps().promote_screened())


@app.command("library-refresh-production")
def library_refresh_production(
    include_screened: bool = typer.Option(
        False, help="also try promoting screened (slow; research pile, not production inventory)"
    ),
):
    """Re-score candidates under the current production gate."""
    print(LibraryOps().refresh_production(include_screened=include_screened))


@app.command("library-promote")
def library_promote(
    name: str,
    gate: str = typer.Option("production", help="reeval gate before promote"),
    status: str = typer.Option("approved"),
):
    ops = LibraryOps()
    report = ops.reevaluate_and_route(name, gate_name=gate)
    if report.get("gate", {}).get("status") == "candidate" or status == "candidate":
        if status == "approved" and report.get("gate", {}).get("passed"):
            print(ops.promote(name, "approved"))
        else:
            print({"eval": report.get("summary"), "status": report.get("gate")})
    else:
        print({"eval": report.get("summary"), "status": report.get("gate"), "promoted": False})


@app.command("library-demote")
def library_demote(name: str, to: str = typer.Option("deprecated"), reason: str = ""):
    print(LibraryOps().demote(name, to=to, reason=reason))  # type: ignore[arg-type]


@app.command("data-status")
def data_status():
    print(DataService().status())


@app.command("db-init")
def db_init():
    from qfactor.db.models import db_path, init_db

    path = init_db()
    print({"database": str(path), "initialized": True})


@app.command("db-import")
def db_import():
    """Import existing parquet/factor_lib into SQLite."""
    from qfactor.db.migrate import import_processed_to_db

    print(import_processed_to_db())


@app.command("db-status")
def db_status_cmd():
    from qfactor.db.models import db_path
    from qfactor.db.repo import Database

    db = Database()
    print({"path": str(db_path()), **db.status()})


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Start API + Web UI."""
    import uvicorn

    uvicorn.run("qfactor.api.app:app", host=host, port=port, reload=False)


@app.command("show-config")
def show_config():
    cfg = get_project_config()
    print(
        {
            "root": str(cfg.root),
            "universe": cfg.universe,
            "frequency": cfg.frequency,
            "factor_lib": str(cfg.path("factor_lib")),
        }
    )


if __name__ == "__main__":
    app()