from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from qfactor.data.dataset import DataService
from qfactor.db.repo import Database
from qfactor.eval.partitions import EvaluationPartitions
from qfactor.settings import ProjectConfig, get_project_config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_json(value: Any) -> Any:
    """Round-trip arbitrary metadata into JSON-safe, stable payloads."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class ExperimentLedger:
    """Append-only audit log for one factor-discovery experiment.

    A discovery run may use an LLM, templates, mutation, or crossover, but every
    attempted expression and rejection is recorded. The ledger deliberately stores
    metadata about model and prompt/config versions, never secrets or raw API keys.
    """

    def __init__(
        self,
        cfg: ProjectConfig | None = None,
        *,
        experiment_id: str | None = None,
        run_dir: Path | None = None,
        db: Database | None = None,
    ):
        self.cfg = cfg or get_project_config()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.experiment_id = experiment_id or f"exp_{stamp}_{uuid4().hex[:10]}"
        self.root = run_dir or self.cfg.path("runs") / "experiments" / self.experiment_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "experiment_manifest.json"
        self.trials_path = self.root / "trials.jsonl"
        self.db = db or Database()
        self._trial_count = 0
        self._manifest: dict[str, Any] = {}

    @property
    def trial_count(self) -> int:
        return self._trial_count

    @property
    def max_trials(self) -> int:
        section = (self.cfg.project.get("experiment") or {}) if self.cfg.project else {}
        return max(1, int(section.get("max_trials", 800)))

    def start(
        self,
        *,
        run_id: str,
        data_version: str | None,
        llm: Any,
        search_config: dict[str, Any],
        date_partitions: dict[str, Any],
    ) -> dict[str, Any]:
        if self.manifest_path.exists():
            self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self._trial_count = int(self._manifest.get("trial_count") or 0)
            return dict(self._manifest)

        cfg_snapshot = {
            "production": self.cfg.project.get("production") or {},
            "experiment": self.cfg.project.get("experiment") or {},
            "eval": self.cfg.eval,
            "universe": self.cfg.universe,
        }
        self._manifest = {
            "schema_version": 1,
            "experiment_id": self.experiment_id,
            "run_id": run_id,
            "state": "running",
            "created_at": _utc_now(),
            "closed_at": None,
            "data_version": data_version,
            "research_only": True,
            "date_partitions": _safe_json(date_partitions),
            "search_config": _safe_json(search_config),
            "search_config_sha256": _json_hash(search_config),
            "config_snapshot": _safe_json(cfg_snapshot),
            "config_sha256": _json_hash(cfg_snapshot),
            "llm": {
                "enabled": bool(getattr(llm, "enabled", False)),
                "model": str(getattr(llm, "model", "")),
                "reasoning_effort": str(getattr(llm, "reasoning_effort", "")),
                "client": type(llm).__name__,
            },
            "trial_count": 0,
            "max_trials": self.max_trials,
            "trial_ledger": str(self.trials_path.relative_to(self.cfg.root)),
            "notes": [
                "Discovery outputs are research-only and cannot be interpreted as sealed OOS evidence.",
                "The ledger records all generated, rejected, and saved candidates; it never stores API keys.",
            ],
        }
        self._write_manifest()
        self.db.create_experiment(self.experiment_id, self._manifest)
        return dict(self._manifest)

    def can_add_trial(self) -> bool:
        return self.trial_count < self.max_trials

    def record_trial(
        self,
        *,
        trial_id: str,
        stage: str,
        outcome: str,
        candidate: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not self._manifest:
            raise RuntimeError("Experiment ledger must be started before recording trials")
        if not self.can_add_trial() and stage == "generated":
            raise RuntimeError(
                f"Experiment {self.experiment_id} exhausted its trial budget ({self.max_trials})"
            )
        candidate = candidate or {}
        event = {
            "event_id": f"{trial_id}:{stage}:{self._trial_count + 1}",
            "experiment_id": self.experiment_id,
            "trial_id": trial_id,
            "stage": stage,
            "outcome": outcome,
            "timestamp": _utc_now(),
            "source": str(candidate.get("source") or "unknown"),
            "name": candidate.get("name"),
            "expression": candidate.get("expression"),
            "mechanism": candidate.get("mechanism"),
            "expr_hash": candidate.get("expr_hash") or candidate.get("_expr_hash"),
            "detail": _safe_json(detail or {}),
        }
        with self.trials_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        if stage == "generated":
            self._trial_count += 1
            self._manifest["trial_count"] = self._trial_count
            self._write_manifest()
        self.db.save_experiment_trial(self.experiment_id, event)

    def close(self, *, state: str, summary: dict[str, Any]) -> dict[str, Any]:
        if not self._manifest:
            raise RuntimeError("Experiment ledger was not started")
        self._manifest["state"] = state
        self._manifest["closed_at"] = _utc_now()
        self._manifest["trial_count"] = self._trial_count
        self._manifest["summary"] = _safe_json(summary)
        self._write_manifest()
        self.db.update_experiment(self.experiment_id, self._manifest)
        return dict(self._manifest)

    def _write_manifest(self) -> None:
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)


def build_date_partitions(cfg: ProjectConfig | None = None) -> dict[str, Any]:
    """Return the frozen experiment windows without claiming OOS when incomplete."""
    cfg = cfg or get_project_config()
    meta = DataService(cfg).status().get("meta") or {}
    raw = ((cfg.eval.get("eval") or {}).get("partitions") or {})
    discovery_start = str(raw.get("discovery_start") or "")
    discovery_end = str(raw.get("discovery_end") or "")
    configured = bool(discovery_start and discovery_end)
    windows: dict[str, Any] = {}
    if configured:
        parts = EvaluationPartitions(
            discovery_start=discovery_start,
            discovery_end=discovery_end,
            selection_start=raw.get("selection_start") or None,
            selection_end=raw.get("selection_end") or None,
            sealed_start=raw.get("sealed_start") or None,
            sealed_end=raw.get("sealed_end") or None,
        )
        windows = parts.as_dict()
    return {
        "data_start": meta.get("start"),
        "data_end": meta.get("end"),
        "state": "configured" if configured else "unconfigured",
        "windows": windows,
        "sealed_oos": {
            "state": "configured" if windows.get("sealed_start") else "unconfigured",
            "note": "Sealed OOS can be consumed only through a frozen acceptance run.",
        },
    }


def require_observational_research_contract(
    cfg: ProjectConfig | None = None,
) -> dict[str, Any]:
    """Validate a bounded non-production data set for interim research.

    This is deliberately separate from ``require_discovery_contract``.  It can
    permit reproducible snapshot price/volume research only when an operator
    explicitly enables it, and returns provenance stating that the result is
    not eligible for production promotion, sealed acceptance, or release.
    """
    cfg = cfg or get_project_config()
    runtime = cfg.project.get("research_runtime") or {}
    if not bool(runtime.get("allow_observational_data", False)):
        raise RuntimeError("observational_research_disabled")

    data = DataService(cfg)
    status = data.status()
    meta = status.get("meta") or {}
    partitions = build_date_partitions(cfg)
    issues: list[str] = []
    if not status.get("has_bars"):
        issues.append("bars_missing")
    if not status.get("has_universe"):
        issues.append("universe_missing")
    if not status.get("data_version"):
        issues.append("data_version_missing")
    if partitions["state"] != "configured":
        issues.append("research_partitions_unconfigured")

    allowed_modes = {
        str(x).strip().lower()
        for x in runtime.get("allowed_universe_modes", ["snapshot"])
    }
    mode = str(meta.get("universe_mode") or "").strip().lower()
    if not mode:
        members_provider = meta.get("members_provider")
        if isinstance(members_provider, dict):
            mode = str(members_provider.get("universe_mode") or "").strip().lower()
    if not mode:
        limitations = str(meta.get("limitations") or "").lower()
        # This inference is intentionally available only to the observational
        # contract. The production contract still requires an explicit PIT mode.
        if "latest snapshot" in limitations:
            mode = "snapshot"
    mode = mode or "unknown"
    if allowed_modes and mode not in allowed_modes:
        issues.append(f"universe_mode_not_allowed:{mode}")

    dates: list[str] = []
    n_codes = 0
    try:
        bars = data.load_bars()
        dates = sorted(
            bars["trade_date"]
            .astype(str)
            .str.replace("-", "", regex=False)
            .str[:8]
            .unique()
        )
        n_codes = int(bars["ts_code"].astype(str).nunique())
    except Exception as exc:
        issues.append(f"bars_unreadable:{exc}")
    min_days = max(1, int(runtime.get("min_trading_days", 252)))
    min_codes = max(1, int(runtime.get("min_securities", 50)))
    if len(dates) < min_days:
        issues.append(f"insufficient_trading_days:{len(dates)}<{min_days}")
    if n_codes < min_codes:
        issues.append(f"insufficient_securities:{n_codes}<{min_codes}")
    if dates and partitions["state"] == "configured":
        bounds = [
            value
            for value in (partitions.get("windows") or {}).values()
            if value is not None
        ]
        if bounds and (min(bounds) < dates[0] or max(bounds) > dates[-1]):
            issues.append("configured_partitions_outside_data_range")

    if issues:
        raise RuntimeError(
            "Observational research is blocked until its explicit research contract passes: "
            + ", ".join(issues)
        )
    return {
        **partitions,
        "contract_kind": "observational_research_only",
        "production_eligible": False,
        "data_version": status.get("data_version"),
        "universe_mode": mode,
        "data_start": dates[0] if dates else meta.get("start"),
        "data_end": dates[-1] if dates else meta.get("end"),
        "n_trading_days": len(dates),
        "n_securities": n_codes,
        "limitations": meta.get("limitations") or [],
    }


def require_discovery_contract(cfg: ProjectConfig | None = None) -> dict[str, Any]:
    """Fail closed before an LLM can search a non-production research sample."""
    cfg = cfg or get_project_config()
    status = DataService(cfg).status()
    meta = status.get("meta") or {}
    production = cfg.eval.get("production") or {}
    partitions = build_date_partitions(cfg)
    issues: list[str] = []
    if str(meta.get("universe_mode") or "").lower() not in {
        str(x).lower() for x in production.get("allowed_universe_modes", ["pit"])
    }:
        issues.append("universe_not_pit")
    source = str(meta.get("circ_mv_source") or "").lower()
    allowed = {str(x).lower() for x in production.get("allowed_circ_mv_sources", [])}
    if allowed and source not in allowed:
        issues.append("circ_mv_not_verified_provider")
    if float(meta.get("daily_basic_coverage") or 0.0) < float(production.get("min_daily_basic_coverage", 0.0)):
        issues.append("daily_basic_coverage_below_contract")
    if production.get("require_execution_data", False):
        requirements = {
            "security_status_coverage": "min_security_status_coverage",
            "limit_price_coverage": "min_limit_price_coverage",
            "adv_20d_coverage": "min_adv_20d_coverage",
            "corporate_action_coverage": "min_corporate_action_coverage",
            "industry_pit_coverage": "min_industry_pit_coverage",
            "risk_exposures_coverage": "min_risk_exposures_coverage",
        }
        for metric, threshold in requirements.items():
            if float(meta.get(metric) or 0.0) < float(production.get(threshold, 1.0)):
                issues.append(f"{metric}_below_contract")
    if partitions["state"] != "configured":
        issues.append("discovery_partitions_unconfigured")
    if issues:
        raise RuntimeError(
            "LLM discovery is blocked until the data/time contract passes: " + ", ".join(issues)
        )
    return partitions
