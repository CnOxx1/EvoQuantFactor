"""One-hour factory observe on the vendor-archive PIT data version.

Prepares the discovery window only (20240102-20251231). Does not loosen
gates and does not invent selection dates. Candidate must stay blocked.

Usage:
  .venv/bin/python runs/_pit_hour_observe.py
  .venv/bin/python runs/_pit_hour_observe.py 3600 pit_hour
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qfactor.agent.experiments import factor_contract_readiness
from qfactor.agent.loop import FactorLoop
from qfactor.data.prepare import DataPrepareService
from qfactor.eval.service import EvalService
from qfactor.factor.cohort import classify_research_cohort
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import get_project_config

THEMES = [
    "amplitude",
    "liquidity",
    "momentum",
    "overnight",
    "reversal",
    "shadow",
    "volatility",
    "volume_price",
]
DISCOVERY_START = "20240102"
DISCOVERY_END = "20251231"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _log(msg: str, log_path: Path) -> None:
    line = f"[{utc_now()}] {msg}"
    try:
        print(line, flush=True)
    except OSError:
        pass
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _counts(rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
    rows = rows if rows is not None else FactorRegistry().list_factors()
    c: Counter[str] = Counter()
    cohorts: Counter[str] = Counter()
    for f in rows:
        c[str(f.get("status") or "unknown")] += 1
        c["total"] += 1
        cohorts[str(classify_research_cohort(f).get("cohort") or "unknown")] += 1
    out = dict(c)
    out.update({f"cohort_{k}": int(v) for k, v in cohorts.items()})
    return out


def _trial_bits(trial: dict[str, Any]) -> dict[str, Any]:
    summary = trial.get("summary") if isinstance(trial.get("summary"), dict) else {}
    metrics = trial.get("metrics") if isinstance(trial.get("metrics"), dict) else summary
    gate = trial.get("gate") if isinstance(trial.get("gate"), dict) else {}
    return {
        "name": trial.get("name") or summary.get("name"),
        "status": trial.get("status") or gate.get("status") or summary.get("status"),
        "mechanism": trial.get("mechanism") or summary.get("mechanism"),
        "rank_ic_mean": metrics.get("rank_ic_mean"),
        "resid_ic_mean": metrics.get("resid_ic_mean"),
        "oos_ic_mean": metrics.get("oos_ic_mean"),
        "universe_mode": metrics.get("universe_mode"),
        "circ_mv_source": metrics.get("circ_mv_source"),
        "industry_pit_coverage": metrics.get("industry_pit_coverage"),
        "data_version": metrics.get("data_version"),
    }


def _collect_trials(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("trials") or result.get("produced") or result.get("saved") or []
    if isinstance(raw, dict):
        raw = raw.get("items") or []
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(_trial_bits(item))
            elif isinstance(item, str):
                out.append({"name": item})
    for key in ("saved", "produced"):
        val = result.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item not in {t.get("name") for t in out}:
                    out.append({"name": item})
    return out


def _library_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = []
    for f in rows:
        cohort = classify_research_cohort(f).get("cohort")
        if cohort != "clean_discovery" or f.get("status") != "screened":
            continue
        s = f.get("summary") if isinstance(f.get("summary"), dict) else {}
        clean.append(
            {
                "name": f.get("name"),
                "rank_ic_mean": s.get("rank_ic_mean"),
                "resid_ic_mean": s.get("resid_ic_mean"),
                "oos_ic_mean": s.get("oos_ic_mean"),
                "universe_mode": s.get("universe_mode"),
                "circ_mv_source": s.get("circ_mv_source"),
                "data_version": s.get("data_version"),
            }
        )
    ics = [float(x["rank_ic_mean"]) for x in clean if x.get("rank_ic_mean") is not None]
    resids = [float(x["resid_ic_mean"]) for x in clean if x.get("resid_ic_mean") is not None]
    return {
        "n_clean_screened": len(clean),
        "mean_rank_ic": float(sum(ics) / len(ics)) if ics else None,
        "mean_resid_ic": float(sum(resids) / len(resids)) if resids else None,
        "n_resid_nonzero": int(sum(1 for v in resids if abs(v) > 1e-12)),
        "top": sorted(clean, key=lambda x: abs(float(x.get("rank_ic_mean") or 0)), reverse=True)[:8],
    }


def main() -> int:
    duration_s = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    tag = str(sys.argv[2]) if len(sys.argv) > 2 else "pit_hour"
    cfg = get_project_config()
    out_dir = cfg.path("runs") / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "console.log"
    status_path = out_dir / "status.json"
    artifact_dir = Path("/opt/cursor/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    deadline = t0 + duration_s
    rows0 = FactorRegistry().list_factors()
    baseline = {
        "counts": _counts(rows0),
        "readiness": factor_contract_readiness(),
        "clean_quality": _library_quality(rows0),
    }
    _write_json(out_dir / "baseline.json", baseline)
    _write_json(artifact_dir / f"{tag}_baseline.json", baseline)
    _log(
        f"start {duration_s}s data_version={baseline['readiness'].get('data_version')} "
        f"research={baseline['readiness'].get('research', {}).get('state')} "
        f"candidate={baseline['readiness'].get('candidate', {}).get('state')}",
        log_path,
    )

    prepare = DataPrepareService(cfg).ensure_research_ready(
        start=DISCOVERY_START,
        end=DISCOVERY_END,
        source="baostock",
        sync=False,
        allow_snapshot_universe=False,
    )
    _write_json(out_dir / "prepare.json", prepare.as_dict())
    _log(
        f"prepare mining_allowed={prepare.mining_allowed} reason={prepare.reason} "
        f"block={prepare.block_reason} universe={prepare.coverage.get('universe_mode')} "
        f"circ_mv={prepare.coverage.get('circ_mv_source')}",
        log_path,
    )
    if not prepare.mining_allowed:
        payload = {
            "state": "blocked",
            "prepare": prepare.as_dict(),
            "baseline": baseline,
            "elapsed_s": time.time() - t0,
        }
        _write_json(status_path, payload)
        _write_json(artifact_dir / f"{tag}_summary.json", payload)
        return 2

    loop = FactorLoop(cfg)
    cycles: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    themes_used: Counter[str] = Counter()
    n_saved = 0
    n_trials = 0
    cycle = 0

    while time.time() < deadline:
        cycle += 1
        theme = THEMES[(cycle - 1) % len(THEMES)]
        remaining = deadline - time.time()
        if remaining < 20:
            _log("time budget exhausted before next cycle", log_path)
            break
        rec: dict[str, Any] = {
            "cycle": cycle,
            "theme": theme,
            "started_at": utc_now(),
            "remaining_s": remaining,
        }
        try:
            result = loop.run(
                rounds=1,
                batch_size=2,
                theme=theme,
                gate_name="research",
                resume=False,
                clean_experiment=True,
            )
            rec["state"] = "ok"
            rec["loop_status"] = result.get("status")
            rec["saved"] = result.get("saved") or result.get("produced") or []
            if isinstance(rec["saved"], dict):
                rec["saved"] = rec["saved"].get("names") or []
            trials = _collect_trials(result)
            rec["trials"] = trials
            rec["n_trials"] = len(trials)
            n_trials += len(trials)
            saved_names = [x for x in rec["saved"] if isinstance(x, str)]
            n_saved += len(saved_names)
            themes_used[theme] += 1
            for trial in trials:
                st = str(trial.get("status") or rec.get("loop_status") or "unknown")
                reasons[st] += 1
        except Exception as exc:
            rec["state"] = "error"
            rec["error"] = str(exc)
            rec["traceback"] = traceback.format_exc()
            reasons["error"] += 1
            _log(f"cycle {cycle} ERROR {exc}", log_path)
        rec["counts"] = _counts()
        rec["elapsed_s"] = time.time() - t0
        rec["finished_at"] = utc_now()
        cycles.append(rec)
        _write_json(status_path, {"cycle": cycle, "elapsed_s": rec["elapsed_s"], "latest": rec})
        _log(
            json.dumps(
                {
                    "cycle": cycle,
                    "state": rec.get("state"),
                    "theme": theme,
                    "status": rec.get("loop_status"),
                    "saved": rec.get("saved"),
                    "counts": rec.get("counts"),
                    "elapsed_s": round(float(rec["elapsed_s"]), 1),
                },
                ensure_ascii=False,
            ),
            log_path,
        )

    rows1 = FactorRegistry().list_factors()
    readiness = factor_contract_readiness()
    ics = [
        float(t["rank_ic_mean"])
        for c in cycles
        for t in c.get("trials") or []
        if t.get("rank_ic_mean") is not None
    ]
    resids = [
        float(t["resid_ic_mean"])
        for c in cycles
        for t in c.get("trials") or []
        if t.get("resid_ic_mean") is not None
    ]
    summary = {
        "started_at": baseline["readiness"].get("data_version"),
        "finished_at": utc_now(),
        "duration_s": duration_s,
        "elapsed_s": time.time() - t0,
        "tag": tag,
        "prepare": prepare.as_dict(),
        "baseline_counts": baseline["counts"],
        "final_counts": _counts(rows1),
        "baseline_clean": baseline["clean_quality"],
        "final_clean": _library_quality(rows1),
        "readiness": readiness,
        "n_cycles": cycle,
        "n_trials": n_trials,
        "n_saved": n_saved,
        "themes": dict(themes_used),
        "theme_sequence": [c.get("theme") for c in cycles],
        "statuses": dict(reasons),
        "ic": {
            "n": len(ics),
            "mean": float(sum(ics) / len(ics)) if ics else None,
            "max": max(ics) if ics else None,
            "n_ge_research_001": int(sum(1 for v in ics if abs(v) >= 0.01)),
            "n_ge_production_002": int(sum(1 for v in ics if abs(v) >= 0.02)),
        },
        "resid_ic": {
            "n": len(resids),
            "mean": float(sum(resids) / len(resids)) if resids else None,
            "max": max(resids) if resids else None,
            "n_nonzero": int(sum(1 for v in resids if abs(v) > 1e-12)),
        },
        "candidate_still_zero": int(_counts(rows1).get("candidate") or 0) == 0,
        "cycles": cycles,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(artifact_dir / f"{tag}_summary.json", summary)
    _log(
        "ONE_HOUR_DONE "
        + json.dumps(
            {
                "n_cycles": cycle,
                "n_trials": n_trials,
                "n_saved": n_saved,
                "statuses": dict(reasons),
                "themes": dict(themes_used),
                "ic": summary["ic"],
                "resid_ic": summary["resid_ic"],
                "candidate": readiness.get("candidate", {}).get("state"),
                "candidate_issues": readiness.get("candidate", {}).get("issues"),
            },
            ensure_ascii=False,
        ),
        log_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
