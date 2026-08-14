from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from qfactor.data.dataset import DataService
from qfactor.db.repo import Database
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
    """Declare available windows without claiming the final OOS is already sealed."""
    cfg = cfg or get_project_config()
    meta = DataService(cfg).status().get("meta") or {}
    train_end = str((cfg.eval.get("eval") or {}).get("train_end") or "")
    return {
        "data_start": meta.get("start"),
        "data_end": meta.get("end"),
        "discovery_end": train_end or None,
        "selection_window": "configured walk-forward folds ending at discovery_end",
        "sealed_oos": {
            "state": "unconfigured",
            "note": "No sealed OOS is claimed until a release-specific acceptance manifest is created.",
        },
    }
