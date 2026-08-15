from __future__ import annotations

"""Supervised, fail-closed runtime for the EvoQuantFactor factory.

The worker deliberately treats discovery, production re-evaluation, sealed
acceptance, and trading release as separate lifecycle gates. A failed data
contract is an auditable idle state, never a reason to relax criteria or create
synthetic production factors.
"""

import argparse
import json
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qfactor.agent.experiments import (
    candidate_contract_readiness,
    discovery_contract_readiness,
)
from qfactor.agent.loop import FactorLoop
from qfactor.data.dataset import DataService
from qfactor.db.repo import Database
from qfactor.factor.ops import LibraryOps
from qfactor.factor.registry import FactorRegistry
from qfactor.factor.release import ReleaseService
from qfactor.settings import ProjectConfig, get_project_config


STATUS_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


class FactoryRuntime:
    """Run controlled factor-factory cycles and persist an auditable heartbeat."""

    def __init__(
        self,
        cfg: ProjectConfig | None = None,
        *,
        interval_seconds: int = 300,
        discovery_every: int = 12,
        screened_every: int = 72,
        llm_ratio: float | None = None,
        runtime_dir: Path | None = None,
    ):
        self.cfg = cfg or get_project_config()
        self.interval_seconds = max(60, int(interval_seconds))
        self.discovery_every = max(1, int(discovery_every))
        self.screened_every = max(1, int(screened_every))
        production = (self.cfg.project.get("production") or {}).get("llm") or {}
        self.llm_ratio = float(production.get("llm_ratio", 0.0) if llm_ratio is None else llm_ratio)
        experiment = self.cfg.project.get("experiment") or {}
        self.clean_experiment = bool(experiment.get("clean_discovery_default", True))
        self.registry = FactorRegistry(self.cfg)
        self.ops = LibraryOps(self.cfg)
        self.release = ReleaseService(self.cfg)
        self.data = DataService(self.cfg)
        self.runtime_dir = runtime_dir or self.cfg.path("runs") / "factory_monitor"
        self.status_path = self.runtime_dir / "status.json"
        self.events_path = self.runtime_dir / "events.jsonl"
        self.stop_path = self.runtime_dir / "STOP"

    def _append_event(self, event: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def lifecycle_counts(self) -> dict[str, int]:
        rows = self.registry.list_factors()
        counts = Counter(str(row.get("status") or "unknown") for row in rows)
        frozen = sealed_passed = tradability_passed = 0
        for row in rows:
            fdir = self.registry.factor_dir(str(row.get("name")))
            if (fdir / "acceptance" / "frozen_definition.json").exists():
                frozen += 1
            acceptance = fdir / "acceptance" / "latest.json"
            if acceptance.exists():
                try:
                    if json.loads(acceptance.read_text(encoding="utf-8")).get("state") == "sealed_oos_passed":
                        sealed_passed += 1
                except (OSError, json.JSONDecodeError):
                    pass
            tradability = fdir / "tradability" / "latest.json"
            if tradability.exists():
                try:
                    if json.loads(tradability.read_text(encoding="utf-8")).get("state") == "tradability_passed":
                        tradability_passed += 1
                except (OSError, json.JSONDecodeError):
                    pass
        active = len(Database().list_releases(state="active"))
        return {
            "total": len(rows),
            "draft": int(counts.get("draft", 0)),
            "screened": int(counts.get("screened", 0)),
            "candidate": int(counts.get("candidate", 0)),
            "approved": int(counts.get("approved", 0)),
            "deprecated": int(counts.get("deprecated", 0)),
            "archived": int(counts.get("archived", 0)),
            "frozen_definition": frozen,
            "sealed_oos_passed": sealed_passed,
            "tradability_passed": tradability_passed,
            "active_release": active,
        }

    def _discovery_contract(self) -> dict[str, Any]:
        readiness = discovery_contract_readiness(self.cfg)
        return {
            **readiness,
            "reason": ", ".join(readiness.get("issues") or []),
        }

    def _candidate_contract(self) -> dict[str, Any]:
        readiness = candidate_contract_readiness(self.cfg)
        return {
            **readiness,
            "reason": ", ".join(readiness.get("issues") or []),
        }

    def run_cycle(self, cycle: int) -> dict[str, Any]:
        """Run exactly one auditable lifecycle pass; exceptions are captured."""
        started = utc_now()
        data_status = self.data.status()
        contract = self._discovery_contract()
        candidate_contract = self._candidate_contract()
        result: dict[str, Any] = {
            "schema_version": STATUS_VERSION,
            "cycle": int(cycle),
            "started_at": started,
            "data_version": data_status.get("data_version"),
            "data_contract": contract,
            "candidate_contract": candidate_contract,
            "counts_before": self.lifecycle_counts(),
            "actions": {},
            "errors": [],
            "warnings": [],
        }

        # Discovery may execute only when complete PIT/time evidence is present,
        # and only on its configured cadence. It remains research-only.
        if contract["state"] == "passed" and cycle % self.discovery_every == 0:
            try:
                result["actions"]["research_discovery"] = FactorLoop(self.cfg).run(
                    rounds=1,
                    batch_size=2,
                    gate_name="research",
                    llm_ratio=self.llm_ratio,
                    llm_review_ratio=0.0,
                    clean_experiment=self.clean_experiment,
                )
            except Exception as exc:
                result["actions"]["research_discovery"] = {"state": "error", "error": str(exc)}
                result["errors"].append({"stage": "research_discovery", "error": str(exc)})
        else:
            result["actions"]["research_discovery"] = {
                "state": "skipped" if contract["state"] == "passed" else "blocked",
                "reason": "cadence" if contract["state"] == "passed" else contract.get("reason"),
            }

        # Re-score the small candidate book every cycle. A larger screened book is
        # intentionally revisited much less often to avoid recurrent search bias.
        try:
            result["actions"]["refresh_candidates"] = self.ops.refresh_production(
                include_screened=False
            )
        except Exception as exc:
            result["actions"]["refresh_candidates"] = {"state": "error", "error": str(exc)}
            result["errors"].append({"stage": "refresh_candidates", "error": str(exc)})
        if candidate_contract["state"] != "passed":
            result["actions"]["recheck_screened"] = {
                "state": "blocked",
                "reason": candidate_contract.get("reason"),
            }
        elif cycle % self.screened_every == 0:
            try:
                result["actions"]["recheck_screened"] = self.ops.promote_screened()
            except Exception as exc:
                result["actions"]["recheck_screened"] = {"state": "error", "error": str(exc)}
                result["errors"].append({"stage": "recheck_screened", "error": str(exc)})
        else:
            result["actions"]["recheck_screened"] = {"state": "skipped", "reason": "cadence"}

        try:
            result["actions"]["trading_releases"] = self.release.export_active()
            result["actions"]["multifactor_inventory"] = self.ops.multifactor_inventory()
        except Exception as exc:
            result["actions"]["inventories"] = {"state": "error", "error": str(exc)}
            result["errors"].append({"stage": "inventories", "error": str(exc)})
        try:
            reconciliation = self.ops.reconcile_state()
            result["actions"]["library_reconciliation"] = reconciliation
            if reconciliation.get("state") != "consistent":
                result["warnings"].append(
                    {
                        "stage": "library_reconciliation",
                        "n_drift": reconciliation.get("n_drift"),
                    }
                )
        except Exception as exc:
            result["actions"]["library_reconciliation"] = {
                "state": "error",
                "error": str(exc),
            }
            result["warnings"].append(
                {"stage": "library_reconciliation", "error": str(exc)}
            )
        result["counts_after"] = self.lifecycle_counts()
        result["finished_at"] = utc_now()
        result["state"] = "degraded" if result["errors"] or result["warnings"] else "ok"
        _write_json_atomic(self.status_path, result)
        self._append_event(result)
        return result

    def run_forever(self, *, start_cycle: int = 1) -> int:
        # An explicit new launch supersedes a stop marker left by an earlier
        # worker. The running loop still observes any STOP created afterwards.
        self.stop_path.unlink(missing_ok=True)
        cycle = max(1, int(start_cycle))
        while not self.stop_path.exists():
            try:
                self.run_cycle(cycle)
            except Exception as exc:  # last-resort worker protection for supervisor restart
                failure = {
                    "schema_version": STATUS_VERSION,
                    "cycle": cycle,
                    "state": "fatal_cycle_error",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "finished_at": utc_now(),
                }
                _write_json_atomic(self.status_path, failure)
                self._append_event(failure)
            cycle += 1
            for _ in range(self.interval_seconds):
                if self.stop_path.exists():
                    break
                time.sleep(1)
        stopped = {
            "schema_version": STATUS_VERSION,
            "state": "stopped",
            "stopped_at": utc_now(),
            "last_status": str(self.status_path),
        }
        _write_json_atomic(self.status_path, stopped)
        self._append_event(stopped)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EvoQuantFactor supervised production runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    run_once = sub.add_parser("run-once")
    run_once.add_argument("--cycle", type=int, default=1)
    run_forever = sub.add_parser("run-forever")
    run_forever.add_argument("--interval-seconds", type=int, default=300)
    run_forever.add_argument("--discovery-every", type=int, default=12)
    run_forever.add_argument("--screened-every", type=int, default=72)
    run_forever.add_argument("--llm-ratio", type=float, default=None)
    run_forever.add_argument("--start-cycle", type=int, default=1)
    sub.add_parser("status")
    sub.add_parser("stop")
    args = parser.parse_args()
    runtime = FactoryRuntime(
        interval_seconds=getattr(args, "interval_seconds", 300),
        discovery_every=getattr(args, "discovery_every", 12),
        screened_every=getattr(args, "screened_every", 72),
        llm_ratio=getattr(args, "llm_ratio", None),
    )
    if args.command == "run-once":
        print(json.dumps(runtime.run_cycle(args.cycle), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "run-forever":
        return runtime.run_forever(start_cycle=args.start_cycle)
    if args.command == "status":
        if runtime.status_path.exists():
            print(runtime.status_path.read_text(encoding="utf-8"))
            return 0
        print(json.dumps({"state": "not_started", "runtime_dir": str(runtime.runtime_dir)}, ensure_ascii=False))
        return 0
    runtime.stop_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.stop_path.write_text(utc_now() + "\n", encoding="utf-8")
    print(json.dumps({"state": "stop_requested", "stop_path": str(runtime.stop_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
