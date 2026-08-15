from __future__ import annotations

from typing import Optional

import typer
from rich import print

from qfactor.agent.loop import FactorLoop, FactorMiningAgent
from qfactor.data.dataset import DataService
from qfactor.eval.service import EvalService
from qfactor.factor.acceptance import AcceptanceService
from qfactor.factor.ops import LibraryOps
from qfactor.factor.registry import FactorRegistry
from qfactor.factor.release import ReleaseService, TradabilityService
from qfactor.seeds import install_seed_factors
from qfactor.settings import get_project_config

app = typer.Typer(help="qfactor: CSI100 price-volume factor library + LLM mining")


@app.command("sync-data")
def sync_data(
    start: str = typer.Option(..., help="YYYYMMDD"),
    end: str = typer.Option(..., help="YYYYMMDD"),
    source: str = typer.Option("auto", help="tushare|baostock|akshare|auto (bars only; PIT evidence is separate)"),
    max_names: Optional[int] = typer.Option(None, help="limit names for smoke test"),
):
    meta = DataService().sync(start, end, source=source, max_names=max_names)  # type: ignore[arg-type]
    print(meta)


@app.command("sync-universe")
def sync_universe(
    start: str = typer.Option(..., help="YYYYMMDD"),
    end: str = typer.Option(..., help="YYYYMMDD"),
):
    """Refresh PIT CSI100 members from archive parquet or Tushare. Does not re-download bars."""
    print(DataService().sync_universe(start, end))


@app.command("ingest-archive")
def ingest_archive(
    role: str = typer.Option(
        ...,
        help="universe|daily_basic|security_status|corporate_actions|risk_exposures|industry",
    ),
    source: str = typer.Option(..., help="vendor csv/xls/xlsx/parquet extract"),
    dest: Optional[str] = typer.Option(None, help="override output parquet path"),
):
    """Normalize a Wind/Choice/RQData/CSIndex extract onto the production archive contract."""
    from pathlib import Path

    from qfactor.data.archive_ingest import ingest_archive_role

    print(ingest_archive_role(role, Path(source), dest=Path(dest) if dest else None))


@app.command("fetch-archive-universe")
def fetch_archive_universe():
    """Download official CSIndex CSI100 files and write a gap-safe universe archive."""
    from qfactor.data.csindex_history import fetch_official_history

    print(fetch_official_history())


@app.command("validate-archive")
def validate_archive(strict: bool = typer.Option(False, help="fail when any role file is missing")):
    """Check registered archive parquet files against the PIT column contract."""
    from qfactor.data.archive_ingest import validate_registered_archives

    report = validate_registered_archives(strict=strict)
    print(report)
    if not report["ok"]:
        raise typer.Exit(code=1)


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
    # Direct production evaluation is diagnostic only. Candidate promotion must
    # go through LibraryOps so correlation and capacity controls are applied.
    report = EvalService().evaluate_and_save(
        name, gate_name=gate, promote=(gate != "production")
    )
    print(report["summary"])
    print({"status": report["gate"]["status"], "checks": report["gate"]["checks"]})


@app.command("freeze-factor")
def freeze_factor(name: str, experiment_id: Optional[str] = typer.Option(None)):
    """Freeze a research definition before it can consume sealed OOS evidence."""
    frozen = AcceptanceService().freeze_definition(name, experiment_id=experiment_id)
    print(
        {
            "name": frozen["name"],
            "definition_hash": frozen["definition_hash"],
            "experiment_id": frozen.get("experiment_id"),
            "frozen_at": frozen["frozen_at"],
        }
    )


@app.command("sealed-accept")
def sealed_accept(
    name: str,
    start: str = typer.Option(..., help="sealed OOS start YYYYMMDD"),
    end: str = typer.Option(..., help="sealed OOS end YYYYMMDD"),
    experiment_id: Optional[str] = typer.Option(None),
):
    """Consume one immutable final-OOS evaluation for a frozen factor definition."""
    out = AcceptanceService().sealed_acceptance(
        name, sealed_start=start, sealed_end=end, experiment_id=experiment_id
    )
    print(
        {
            "acceptance_id": out["acceptance_id"],
            "name": out["name"],
            "state": out["state"],
            "data_version": out.get("data_version"),
            "sealed_window": out["sealed_window"],
        }
    )


@app.command("promote", hidden=True)
def promote(name: str, status: str = typer.Option("approved")):
    raise typer.BadParameter(
        "Direct status promotion is disabled. Use 'library-promote NAME --gate production' "
        "so the production gate and library controls are enforced."
    )


@app.command("mine")
def mine(
    theme: str = typer.Option(..., help="e.g. reversal / liquidity / volatility"),
    max_iters: int = typer.Option(3),
    gate: str = typer.Option(
        "research", help="must remain research; production is handled by library operations"
    ),
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
    gate: str = typer.Option(
        "research", help="must remain research; production is handled by library operations"
    ),
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


@app.command("assess-tradability")
def assess_tradability(name: str):
    """Diagnose execution-data readiness; does not claim a factor is tradable."""
    out = TradabilityService().assess_readiness(name)
    print({"name": name, "state": out["state"], "reasons": out["reasons"]})


@app.command("simulate-tradability")
def simulate_tradability(name: str):
    """Run T+1, non-overlapping execution ledger; it remains fail-closed on missing PIT constraints."""
    out = TradabilityService().simulate(name)
    execution = out.get("execution") or {}
    print(
        {
            "name": name,
            "state": out["state"],
            "reasons": out.get("reasons", []),
            "n_filled": execution.get("n_filled"),
            "fill_rate": execution.get("fill_rate"),
            "net_long_short_mean": execution.get("net_long_short_mean"),
        }
    )


@app.command("publish-release")
def publish_release(name: str):
    """Publish a factor only after production, sealed OOS, and tradability evidence pass."""
    out = ReleaseService().publish(name)
    print(
        {
            "name": name,
            "state": out["state"],
            "release_id": out.get("release_id"),
            "reasons": out.get("reasons", []),
        }
    )


@app.command("export-trading-releases")
def export_trading_releases(
    output: Optional[str] = typer.Option(None, help="output JSON path; defaults to factor_lib"),
):
    """Export the sole production contract intended for downstream trading modules."""
    out = ReleaseService().export_active(output=output)
    print({"path": out["path"], "data_version": out["data_version"], "n_active": out["n_active"]})


@app.command("library-export-multifactor")
def library_export_multifactor(
    output: Optional[str] = typer.Option(None, help="output JSON path; defaults to factor_lib"),
):
    """Export only data-version-pinned, production-passing strategy inputs."""
    inventory = LibraryOps().export_multifactor_inventory(output=output)
    print(
        {
            "path": inventory["path"],
            "data_version": inventory["data_version"],
            "n_eligible": inventory["n_eligible"],
            "n_excluded": inventory["n_excluded"],
        }
    )


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


@app.command("library-reconcile")
def library_reconcile():
    """Report catalog/spec/report/SQLite drift; never repairs automatically."""
    from qfactor.factor.reconcile import reconcile_library_state

    print(reconcile_library_state())


@app.command("data-status")
def data_status():
    print(DataService().status())


@app.command("data-contract-readiness")
def data_contract_readiness():
    """Show concise PIT/time blockers without starting discovery."""
    from qfactor.agent.experiments import discovery_contract_readiness

    print(discovery_contract_readiness())


@app.command("db-init")
def db_init():
    from qfactor.db.models import init_db

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