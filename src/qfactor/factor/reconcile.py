from __future__ import annotations

import json
from typing import Any

from qfactor.db.repo import Database
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig


def reconcile_library_state(
    cfg: ProjectConfig | None = None,
    *,
    registry: FactorRegistry | None = None,
    db: Database | None = None,
) -> dict[str, Any]:
    """Report catalog/spec/report/SQLite drift without repairing production state."""
    registry = registry or FactorRegistry(cfg)
    db = db or Database()
    catalog = {str(row.get("name")): row for row in registry.list_factors()}
    db_rows = {str(row.get("name")): row for row in db.list_factors()}
    drift: list[dict[str, Any]] = []

    for name, row in sorted(catalog.items()):
        try:
            spec = registry.load_spec(name)
        except Exception as exc:
            drift.append({"name": name, "kind": "missing_or_invalid_spec", "detail": str(exc)})
            continue
        if str(row.get("status")) != str(spec.status):
            drift.append(
                {
                    "name": name,
                    "kind": "catalog_spec_status_mismatch",
                    "catalog": row.get("status"),
                    "spec": spec.status,
                }
            )
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        if summary.get("library_status") and summary.get("library_status") != row.get("status"):
            drift.append(
                {
                    "name": name,
                    "kind": "catalog_summary_status_mismatch",
                    "catalog": row.get("status"),
                    "summary": summary.get("library_status"),
                }
            )
        latest_path = registry.factor_dir(name) / "reports" / "latest.json"
        file_report: dict[str, Any] | None = None
        if latest_path.exists():
            try:
                file_report = json.loads(latest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                drift.append({"name": name, "kind": "invalid_latest_report", "detail": str(exc)})
        db_row = db_rows.get(name)
        if db_row is None:
            drift.append({"name": name, "kind": "missing_db_factor"})
        elif str(db_row.get("status")) != str(row.get("status")):
            drift.append(
                {
                    "name": name,
                    "kind": "catalog_db_status_mismatch",
                    "catalog": row.get("status"),
                    "database": db_row.get("status"),
                }
            )
        db_report = db.get_latest_report(name)
        if file_report is not None and db_report is None:
            drift.append({"name": name, "kind": "missing_db_latest_report"})
        elif file_report is not None and db_report is not None:
            file_status = (file_report.get("gate") or {}).get("status")
            db_status = (db_report.get("gate") or {}).get("status")
            if file_status != db_status:
                drift.append(
                    {
                        "name": name,
                        "kind": "file_db_report_status_mismatch",
                        "file": file_status,
                        "database": db_status,
                    }
                )

    for name in sorted(set(db_rows) - set(catalog)):
        drift.append({"name": name, "kind": "orphan_db_factor"})

    return {
        "contract_version": "factor_library_reconciliation_v1",
        "state": "consistent" if not drift else "drift_detected",
        "repair_performed": False,
        "n_catalog": len(catalog),
        "n_database": len(db_rows),
        "n_drift": len(drift),
        "drift": drift,
    }
