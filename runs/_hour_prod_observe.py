#!/usr/bin/env python3
"""One-hour observation of clean-factory factor production."""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from qfactor.agent.experiments import factor_contract_readiness
from qfactor.agent.supervisor import FactoryRuntime
from qfactor.factor.ops import LibraryOps
from qfactor.factor.registry import FactorRegistry

OUT_DIR = Path("runs/hour_prod_observe")
SUMMARY = Path("runs/hour_prod_observe_summary.json")
ARTIFACT = Path("/opt/cursor/artifacts/hour_prod_observe_summary.json")
DURATION_S = 3600


def _lesson(item: dict) -> dict:
    detail = item.get("detail") or {}
    return {
        "mechanism": item.get("mechanism"),
        "reason": item.get("reason"),
        "expression": item.get("expression"),
        "rank_ic_mean": detail.get("rank_ic_mean"),
        "max_corr": detail.get("max_corr"),
        "status": detail.get("status"),
    }


def _discovery_summary(action: object) -> dict:
    if not isinstance(action, dict):
        return {"state": "missing"}
    if action.get("state") in {"skipped", "blocked", "error"}:
        return {
            "state": action.get("state"),
            "reason": action.get("reason") or action.get("error"),
            "trial_count": 0,
            "produced": [],
            "saved_total": [],
            "lessons": [],
        }
    lessons = [_lesson(x) for x in (action.get("lessons_tail") or []) if isinstance(x, dict)]
    return {
        "state": action.get("status") or action.get("experiment_state") or "ran",
        "run_id": action.get("run_id"),
        "experiment_id": action.get("experiment_id"),
        "theme": action.get("round_theme_last") or action.get("theme"),
        "clean_experiment": action.get("clean_experiment"),
        "trial_count": action.get("trial_count") or len(lessons),
        "produced": action.get("produced") or [],
        "saved_total": action.get("saved_total") or [],
        "lessons": lessons,
        "recent_themes": action.get("recent_themes") or [],
        "cold_start": action.get("cold_start"),
    }


def _aggregate(cycles: list[dict], trials: list[dict]) -> dict:
    ics = [float(t["rank_ic_mean"]) for t in trials if t.get("rank_ic_mean") is not None]
    return {
        "n_cycles": len(cycles),
        "n_discovery_runs": sum(1 for c in cycles if (c.get("discovery") or {}).get("state") not in {"skipped", "blocked", "error", "missing"}),
        "n_trials": len(trials),
        "n_saved": sum(len((c.get("discovery") or {}).get("saved_total") or []) for c in cycles),
        "n_produced": sum(len((c.get("discovery") or {}).get("produced") or []) for c in cycles),
        "reasons": dict(Counter(str(t.get("reason") or "unknown") for t in trials)),
        "mechanisms": dict(Counter(str(t.get("mechanism") or "unknown") for t in trials)),
        "gate_status": dict(Counter(str(t.get("status") or "unknown") for t in trials)),
        "themes": dict(Counter(str((c.get("discovery") or {}).get("theme") or "none") for c in cycles)),
        "theme_sequence": [
            str((c.get("discovery") or {}).get("theme") or "none") for c in cycles
        ],
        "n_unique_themes": len(
            {
                str((c.get("discovery") or {}).get("theme") or "")
                for c in cycles
                if (c.get("discovery") or {}).get("theme")
            }
        ),
        "ic": {
            "n": len(ics),
            "mean": (sum(ics) / len(ics)) if ics else None,
            "max": max(ics) if ics else None,
            "min": min(ics) if ics else None,
            "n_ge_research_001": sum(1 for x in ics if abs(x) >= 0.01),
            "n_ge_production_002": sum(1 for x in ics if abs(x) >= 0.02),
        },
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    runtime = FactoryRuntime(
        interval_seconds=60,
        discovery_every=1,
        runtime_dir=OUT_DIR,
    )
    deadline = time.monotonic() + DURATION_S
    out = {
        "started_at": time.time(),
        "duration_s": DURATION_S,
        "mode": "clean_factory_hour_observe",
        "baseline_readiness": factor_contract_readiness(),
        "baseline_counts": runtime.lifecycle_counts(),
        "cycles": [],
        "trials": [],
    }
    print("[hour-prod] start 3600s discovery_every=1", flush=True)
    cycle = 1
    while time.monotonic() < deadline:
        result = runtime.run_cycle(cycle)
        discovery = _discovery_summary((result.get("actions") or {}).get("research_discovery"))
        rows = FactorRegistry().existing_summaries()
        inventory = LibraryOps().multifactor_inventory()
        row = {
            "cycle": cycle,
            "state": result.get("state"),
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
            "research": (result.get("data_contract") or {}).get("state"),
            "candidate": (result.get("candidate_contract") or {}).get("state"),
            "discovery": discovery,
            "counts": result.get("counts_after"),
            "cohorts": dict(Counter(str(x.get("cohort")) for x in rows)),
            "parent_eligible": sum(bool(x.get("parent_eligible")) for x in rows),
            "candidate_export": {
                "n_eligible": inventory.get("n_eligible"),
                "tradable": inventory.get("tradable"),
            },
            "recent_themes": result.get("recent_themes")
            or list(getattr(runtime, "recent_themes", None) or []),
            "errors": result.get("errors") or [],
            "warnings": result.get("warnings") or [],
        }
        out["cycles"].append(row)
        out["trials"].extend(discovery.get("lessons") or [])
        out["aggregate"] = _aggregate(out["cycles"], out["trials"])
        SUMMARY.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            {
                "cycle": cycle,
                "state": row["state"],
                "research": row["research"],
                "theme": discovery.get("theme"),
                "recent_themes": row.get("recent_themes"),
                "trials": discovery.get("trial_count"),
                "status": discovery.get("state"),
                "saved": discovery.get("saved_total"),
                "counts": row["counts"],
                "elapsed_s": round(time.time() - out["started_at"], 1),
            },
            flush=True,
        )
        cycle += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(60.0, remaining))

    out["finished_at"] = time.time()
    out["elapsed_s"] = out["finished_at"] - out["started_at"]
    out["final_readiness"] = factor_contract_readiness()
    out["final_counts"] = runtime.lifecycle_counts()
    out["aggregate"] = _aggregate(out["cycles"], out["trials"])
    text = json.dumps(out, ensure_ascii=False, indent=2)
    SUMMARY.write_text(text, encoding="utf-8")
    ARTIFACT.write_text(text, encoding="utf-8")
    print("[hour-prod] ONE_HOUR_DONE", json.dumps(out["aggregate"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
