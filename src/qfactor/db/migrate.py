from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qfactor.db.repo import Database
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


def import_processed_to_db(cfg: ProjectConfig | None = None) -> dict[str, Any]:
    """One-shot import from parquet/json/factor_lib into SQLite."""
    cfg = cfg or get_project_config()
    db = Database()
    out: dict[str, Any] = {}

    cal = cfg.path("data_processed") / "calendar" / "trade_cal.parquet"
    if cal.exists():
        import pandas as pd

        out["calendar"] = db.replace_calendar(pd.read_parquet(cal))

    univ = cfg.path("data_processed") / "universe" / cfg.universe / "members.parquet"
    if univ.exists():
        import pandas as pd

        out["universe"] = db.replace_universe(cfg.universe, pd.read_parquet(univ))

    bars = cfg.path("data_processed") / "bars" / "daily" / "bars.parquet"
    if bars.exists():
        import pandas as pd

        out["bars"] = db.upsert_bars(pd.read_parquet(bars))

    industry = cfg.path("data_processed") / "meta" / "industry.parquet"
    if industry.exists():
        import pandas as pd

        out["industry"] = db.replace_industry(pd.read_parquet(industry))

    meta_path = cfg.path("data_processed") / "data_version.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        db.save_data_version(meta)
        out["data_version"] = meta.get("data_version")

    reg = FactorRegistry(cfg)
    n_factors = 0
    for item in reg.list_factors():
        name = item["name"]
        try:
            spec = reg.load_spec(name).model_dump()
        except Exception:
            spec = {}
        db.upsert_factor(item, spec)
        latest = reg.factor_dir(name) / "reports" / "latest.json"
        if latest.exists():
            report = json.loads(latest.read_text(encoding="utf-8"))
            db.save_factor_report(name, report)
        n_factors += 1
    out["factors"] = n_factors

    cp = cfg.path("runs") / "checkpoints" / "loop_csi100.json"
    if cp.exists():
        payload = json.loads(cp.read_text(encoding="utf-8"))
        db.save_checkpoint("loop_csi100", payload)
        out["checkpoint"] = True

    out["db_status"] = db.status()
    return out
