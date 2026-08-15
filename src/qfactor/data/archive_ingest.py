from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qfactor.data.vendor_normalize import normalize_panel
from qfactor.settings import ProjectConfig, get_project_config, get_settings

ROLE_TO_ARCHIVE_KEY = {
    "universe": "universe_history",
    "daily_basic": "daily_basic",
    "security_status": "security_status",
    "corporate_actions": "corporate_actions",
    "risk_exposures": "risk_exposures",
    "industry": "industry_history",
}

ROLE_SPECS: dict[str, dict[str, Any]] = {
    "universe": {
        "required": ("trade_date", "ts_code"),
        "output": ("trade_date", "ts_code", "weight"),
    },
    "daily_basic": {
        "required": ("trade_date", "ts_code", "circ_mv"),
        "output": (
            "trade_date",
            "ts_code",
            "circ_mv",
            "turnover_rate",
            "free_float_shares",
            "adv_20d",
        ),
    },
    "security_status": {
        "required": ("trade_date", "ts_code", "is_st", "is_suspended", "limit_up", "limit_down"),
        "output": ("trade_date", "ts_code", "is_st", "is_suspended", "limit_up", "limit_down"),
    },
    "corporate_actions": {
        "required": ("trade_date", "ts_code", "corporate_action"),
        "output": ("trade_date", "ts_code", "corporate_action", "adj_factor_vendor"),
    },
    "risk_exposures": {
        "required": ("trade_date", "ts_code"),
        "output": None,
        "require_extra": True,
    },
    "industry": {
        "required": ("trade_date", "ts_code", "industry"),
        "output": ("trade_date", "ts_code", "industry"),
    },
}


def archive_path_for_role(role: str, cfg: ProjectConfig | None = None) -> Path | None:
    cfg = cfg or get_project_config()
    key = ROLE_TO_ARCHIVE_KEY.get(role)
    if key is None:
        raise ValueError(f"Unknown archive role: {role}")
    raw = (cfg.data_sources.get("archive") or {}).get(key)
    if not raw:
        return None
    return (cfg.root / str(raw)).resolve()


def archive_file_ready(path: Path | None) -> bool:
    return bool(path is not None and path.exists() and path.stat().st_size > 0)


def resolve_evidence_provider(
    role: str,
    cfg: ProjectConfig | None = None,
    *,
    tushare_token: str | None = None,
) -> str | None:
    """Resolve a PIT evidence provider without silent snapshot/estimate fallback.

    auto = Tushare when a token exists, otherwise archive when that role's file
    is present. Missing files stay unresolved so discovery remains fail-closed.
    """
    cfg = cfg or get_project_config()
    providers = cfg.data_sources.get("providers") or {}
    source = str(providers.get(role, "auto")).strip().lower()
    if source in {"", "none", "disabled"}:
        return None
    if source == "auto":
        token = get_settings().tushare_token if tushare_token is None else tushare_token
        if token:
            return "tushare"
        if archive_file_ready(archive_path_for_role(role, cfg)):
            return "archive"
        return None
    if source == "rqdata":
        raise RuntimeError(
            "providers.%s=rqdata is reserved; export RQData to archive parquet "
            "and set the role to archive" % role
        )
    return source


def read_vendor_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".xls", ".xlsx"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported archive input type: {path.suffix}")


def validate_archive_frame(df: pd.DataFrame, role: str) -> dict[str, Any]:
    spec = ROLE_SPECS.get(role)
    if spec is None:
        raise ValueError(f"Unknown archive role: {role}")
    issues: list[str] = []
    warnings: list[str] = []
    if df is None or df.empty:
        issues.append("empty")
        return {
            "role": role,
            "ok": False,
            "n_rows": 0,
            "n_keys": 0,
            "issues": issues,
            "warnings": warnings,
        }
    required = set(spec["required"])
    missing = sorted(required - set(df.columns))
    if missing:
        issues.append("missing_columns:" + ",".join(missing))
    n_rows = int(len(df))
    n_keys = 0
    n_dup = 0
    if {"trade_date", "ts_code"}.issubset(df.columns):
        raw_keys = df[["trade_date", "ts_code"]]
        invalid = raw_keys.isna().any(axis=1) | raw_keys["ts_code"].astype(str).str.strip().eq("")
        if bool(invalid.any()):
            issues.append("unmappable_keys")
        keys = raw_keys.loc[~invalid]
        n_keys = int(len(keys.drop_duplicates()))
        n_dup = int(len(keys) - n_keys)
        if n_dup:
            issues.append("duplicate_keys")
    if spec.get("require_extra"):
        extra = [c for c in df.columns if c not in {"trade_date", "ts_code"}]
        if not extra:
            issues.append("risk_exposures_need_model_column")
    if role == "corporate_actions" and "corporate_action" in df.columns:
        values = df["corporate_action"].astype(str).str.lower()
        if not values.eq("none").any():
            warnings.append("corporate_action_missing_none_rows")
    if role == "daily_basic" and "adv_20d" in df.columns and df["adv_20d"].notna().mean() == 0:
        warnings.append("adv_20d_absent_sync_may_derive")
    dates = sorted(df["trade_date"].dropna().astype(str).unique().tolist()) if "trade_date" in df.columns else []
    return {
        "role": role,
        "ok": not issues,
        "n_rows": n_rows,
        "n_keys": n_keys,
        "n_duplicate_keys": n_dup,
        "n_dates": len(dates),
        "date_start": dates[0] if dates else None,
        "date_end": dates[-1] if dates else None,
        "issues": issues,
        "warnings": warnings,
    }


def _select_output(df: pd.DataFrame, role: str) -> pd.DataFrame:
    spec = ROLE_SPECS[role]
    cols = spec.get("output")
    if cols is None:
        keep = ["trade_date", "ts_code"] + [
            c for c in df.columns if c not in {"trade_date", "ts_code"}
        ]
        return df[keep].copy()
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            out[col] = pd.NA
    return out[list(cols)].copy()


def ingest_archive_role(
    role: str,
    source: Path,
    cfg: ProjectConfig | None = None,
    dest: Path | None = None,
) -> dict[str, Any]:
    """Normalize a vendor extract onto the contract parquet for one role.

    Does not synthesize ST/suspend/limit/industry/circ_mv. Invalid files fail.
    """
    cfg = cfg or get_project_config()
    if role not in ROLE_SPECS:
        raise ValueError(f"Unknown archive role: {role}")
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(src)
    raw = read_vendor_table(src)
    frame = normalize_panel(raw)
    report = validate_archive_frame(frame, role)
    if not report["ok"]:
        raise ValueError(
            f"archive ingest {role} failed: " + ", ".join(report["issues"])
        )
    out = _select_output(frame, role).drop_duplicates(["trade_date", "ts_code"])
    target = dest or archive_path_for_role(role, cfg)
    if target is None:
        raise RuntimeError(f"No archive path configured for role {role}")
    target.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(target, index=False)
    report["path"] = str(target)
    report["source"] = str(src)
    report["n_rows"] = int(len(out))
    report["n_keys"] = int(len(out))
    return report


def validate_registered_archives(
    cfg: ProjectConfig | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    cfg = cfg or get_project_config()
    roles: dict[str, Any] = {}
    issues: list[str] = []
    for role in ROLE_SPECS:
        path = archive_path_for_role(role, cfg)
        if not archive_file_ready(path):
            item = {
                "role": role,
                "path": str(path) if path else None,
                "state": "missing",
                "ok": False,
                "issues": ["file_missing"],
            }
            roles[role] = item
            if strict:
                issues.append(f"{role}:file_missing")
            continue
        frame = normalize_panel(read_vendor_table(path))  # type: ignore[arg-type]
        report = validate_archive_frame(frame, role)
        report["path"] = str(path)
        report["state"] = "ok" if report["ok"] else "invalid"
        roles[role] = report
        if not report["ok"]:
            issues.extend(f"{role}:{x}" for x in report["issues"])
    return {
        "ok": not issues,
        "strict": strict,
        "n_ready": sum(1 for r in roles.values() if r.get("state") == "ok"),
        "n_roles": len(roles),
        "roles": roles,
        "issues": issues,
    }
