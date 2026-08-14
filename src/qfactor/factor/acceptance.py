from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from qfactor.data.dataset import DataService
from qfactor.db.repo import Database
from qfactor.eval.multiple_testing import familywise_ic_audit
from qfactor.eval.partitions import EvaluationPartitions
from qfactor.eval.service import EvalService
from qfactor.factor.provenance import definition_hash, definition_payload
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AcceptanceService:
    """Freeze definitions and run one-time sealed acceptance evaluations.

    Sealing is an operational control: it records the exact definition, data
    version, and date range before evaluation. Any future definition or data
    change requires a new acceptance id rather than reusing a positive result.
    """

    def __init__(
        self,
        cfg: ProjectConfig | None = None,
        *,
        registry: FactorRegistry | None = None,
        evaluator: EvalService | None = None,
        db: Database | None = None,
    ):
        self.cfg = cfg or get_project_config()
        self.registry = registry or FactorRegistry(self.cfg)
        self.evaluator = evaluator
        self.db = db or Database()

    def freeze_definition(self, name: str, *, experiment_id: str | None = None) -> dict[str, Any]:
        spec = self.registry.load_spec(name)
        code_path = self.registry.factor_dir(name) / "factor.py"
        code_sha256 = hashlib.sha256(code_path.read_bytes()).hexdigest()
        frozen = {
            "schema_version": 1,
            "name": name,
            "definition_hash": definition_hash(spec),
            "code_sha256": code_sha256,
            "definition": definition_payload(spec),
            "spec_version": spec.version,
            "frozen_at": _utc_now(),
            "experiment_id": experiment_id or (spec.params or {}).get("experiment_id"),
        }
        root = self.registry.factor_dir(name) / "acceptance"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "frozen_definition.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("definition_hash") != frozen["definition_hash"]:
                raise RuntimeError(
                    "Definition changed after freeze. Create a new factor version instead of overwriting sealed evidence."
                )
            return existing
        path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
        return frozen

    def sealed_acceptance(
        self,
        name: str,
        *,
        sealed_start: str,
        sealed_end: str,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        if not sealed_start or not sealed_end or sealed_start >= sealed_end:
            raise ValueError("sealed_start must be before sealed_end")
        frozen = self.freeze_definition(name, experiment_id=experiment_id)
        latest_path = self.registry.factor_dir(name) / "acceptance" / "latest.json"
        if latest_path.exists():
            previous = json.loads(latest_path.read_text(encoding="utf-8"))
            if previous.get("definition_hash") == frozen["definition_hash"]:
                raise RuntimeError(
                    "This frozen definition already consumed its sealed acceptance run; create a new version for another test."
                )

        status = DataService(self.cfg).status()
        data_version = status.get("data_version")
        data_end = str((status.get("meta") or {}).get("end") or "")
        if data_end and sealed_end > data_end:
            raise RuntimeError(
                f"Sealed end {sealed_end} exceeds available data end {data_end}"
            )
        part_cfg = (self.cfg.eval.get("eval") or {}).get("partitions") or {}
        discovery_start = str(part_cfg.get("discovery_start") or "")
        discovery_end = str(part_cfg.get("discovery_end") or "")
        if not discovery_start or not discovery_end:
            raise RuntimeError(
                "Configure eval.partitions.discovery_start/discovery_end before consuming sealed OOS."
            )
        expected_start = str(part_cfg.get("sealed_start") or sealed_start)
        expected_end = str(part_cfg.get("sealed_end") or sealed_end)
        if expected_start != sealed_start or expected_end != sealed_end:
            raise RuntimeError("sealed window must match the frozen eval.partitions contract")
        EvaluationPartitions(
            discovery_start=discovery_start,
            discovery_end=discovery_end,
            selection_start=part_cfg.get("selection_start"),
            selection_end=part_cfg.get("selection_end"),
            sealed_start=sealed_start,
            sealed_end=sealed_end,
        ).validate()

        evaluator = self.evaluator or EvalService(self.cfg)
        factor = self.registry.load_factor(name)
        report = evaluator.evaluate_factor(
            factor,
            gate_name="production",
            interval_start=sealed_start,
            interval_end=sealed_end,
            sealed=True,
        )

        experiment_ref = frozen.get("experiment_id")
        trial_events = self.db.list_experiment_trials(str(experiment_ref)) if experiment_ref else []
        generated_trials = sum(1 for event in trial_events if event.get("stage") == "generated")
        if generated_trials > 0:
            selection_audit = familywise_ic_audit(
                report.get("metrics") or {}, n_trials=generated_trials
            )
        else:
            selection_audit = {
                "contract_version": "familywise_ic_audit_v1",
                "state": "familywise_failed",
                "passed": False,
                "n_trials": 0,
                "reason": "missing_experiment_trial_ledger",
            }
        acceptance_id = f"acc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:10]}"
        passed = bool((report.get("gate") or {}).get("status") == "candidate" and selection_audit.get("passed"))
        payload = {
            "schema_version": 1,
            "acceptance_id": acceptance_id,
            "name": name,
            "state": "sealed_oos_passed" if passed else "sealed_oos_failed",
            "created_at": _utc_now(),
            "definition_hash": frozen["definition_hash"],
            "data_version": data_version,
            "experiment_id": experiment_ref,
            "selection_bias_audit": selection_audit,
            "sealed_window": {"start": sealed_start, "end": sealed_end},
            "frozen_definition": frozen,
            "report": report,
            "note": "One-time sealed acceptance. Any definition or data-version change requires a new acceptance run.",
        }
        root = self.registry.factor_dir(name) / "acceptance"
        out = root / f"{acceptance_id}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        self.db.save_acceptance(acceptance_id, payload)
        return payload

    def latest(self, name: str) -> dict[str, Any] | None:
        path = self.registry.factor_dir(name) / "acceptance" / "latest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
