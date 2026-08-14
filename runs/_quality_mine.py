"""Longer quality-oriented mining: wall-clock loop, then production re-eval.

Usage:
  python runs/_quality_mine.py              # 7200s, two_hour
  python runs/_quality_mine.py 10800 three_hour
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from qfactor.agent.diversity import expression_fingerprint
from qfactor.agent.graph import _dsl_factor_code
from qfactor.agent.loop import FactorLoop, graph_rounds_for_budget
from qfactor.eval.gate import USABLE_STATUSES
from qfactor.eval.service import EvalService
from qfactor.factor.base import FactorSpec
from qfactor.factor.ops import LibraryOps
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import get_project_config

# Previous session produced this production-gate passer but did not commit it.
_RESTORE_CANDIDATES = [
    {
        "name": "volume_price_llm_111054_9129",
        "mechanism": "volume_price",
        "expression": "sub(rank(sub(close, open)), rank(add(upper_shadow, lower_shadow)))",
        "hypothesis": (
            "当日实体方向（close-open）与上下影线之和的截面排序差："
            "短实体+长影线 vs 长实体+短影线，刻画量价博弈后的 5 日收益。"
        ),
        "source": "llm",
    }
]


def _status_counts() -> dict[str, int]:
    c: Counter[str] = Counter()
    for f in FactorRegistry().list_factors():
        c[str(f.get("status") or "unknown")] += 1
        c["total"] += 1
    return dict(c)


def _safe_print(msg: str, log_path: Path | None = None) -> None:
    try:
        print(msg, flush=True)
    except OSError:
        pass
    if log_path is None:
        return
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def _usable_rows() -> list[dict]:
    rows = []
    root = get_project_config().root
    for f in FactorRegistry().list_factors():
        if str(f.get("status")) not in USABLE_STATUSES:
            continue
        summary = f.get("summary") if isinstance(f.get("summary"), dict) else {}
        spec_path = root / str(f.get("path") or "") / "spec.yaml"
        expression = None
        mechanism = f.get("category")
        if spec_path.exists():
            import yaml

            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            expression = spec.get("expression")
            mechanism = spec.get("mechanism") or mechanism
        rows.append(
            {
                "name": f.get("name"),
                "status": f.get("status"),
                "mechanism": mechanism,
                "source": f.get("source"),
                "expression": expression,
                "train_rank_ic_mean": summary.get("train_rank_ic_mean"),
                "rank_ic_mean": summary.get("rank_ic_mean"),
                "icir_nw": summary.get("icir_nw"),
                "resid_ic_mean": summary.get("resid_ic_mean"),
                "oos_ic_mean": summary.get("oos_ic_mean"),
                "max_corr": summary.get("max_corr"),
                "cost_adjusted_ls": summary.get("cost_adjusted_ls"),
                "coverage": summary.get("coverage"),
            }
        )
    rows.sort(key=lambda r: (str(r.get("mechanism") or ""), str(r.get("name") or "")))
    return rows


def write_usable_inventory(path: Path) -> dict:
    rows = _usable_rows()
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "n_usable": len(rows),
        "mechanisms": sorted({str(r.get("mechanism") or "") for r in rows}),
        "factors": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def restore_known_candidates(log_path: Path | None = None) -> list[dict]:
    """Re-install known production passers lost when library churn was not committed."""
    reg = FactorRegistry()
    existing = {str(f.get("name")) for f in reg.list_factors()}
    ev = EvalService()
    out: list[dict] = []
    for item in _RESTORE_CANDIDATES:
        name = str(item["name"])
        expr = str(item["expression"])
        if name in existing:
            _safe_print(f"[quality-mine] restore skip existing {name}", log_path)
            out.append({"name": name, "action": "exists"})
            continue
        _safe_print(f"[quality-mine] restore eval {name} {expr}", log_path)
        report = ev.evaluate_dsl(expr, name, gate_name="production")
        gate = report.get("gate") or {}
        status = str(gate.get("status") or "reject")
        fp = expression_fingerprint(expr)
        code = _dsl_factor_code(
            name=name,
            expression=expr,
            mechanism=str(item["mechanism"]),
            hypothesis=str(item["hypothesis"]),
        )
        spec = FactorSpec(
            name=name,
            version="0.1.0",
            status=status if status in {"screened", "candidate", "approved"} else "draft",
            family="price_volume",
            category=str(item["mechanism"]),
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            tags=["dsl", "loop", "langgraph", "llm", "restored"],
            hypothesis=str(item["hypothesis"]),
            entry_gate="production",
            expression=expr,
            mechanism=str(item["mechanism"]),
            expr_hash=fp["expr_hash"],
        )
        if status in {"screened", "candidate", "approved"}:
            reg.save_factor_files(spec, code, source=str(item["source"]), report=report)
        else:
            spec.status = "draft"
            reg.save_factor_files(spec, code, source=str(item["source"]), report=report)
        rec = {
            "name": name,
            "action": "restored",
            "status": spec.status,
            "passed": bool(gate.get("passed")),
            "fail": gate.get("fail"),
            "rank_ic_mean": (report.get("summary") or {}).get("rank_ic_mean"),
        }
        out.append(rec)
        _safe_print(f"[quality-mine] restore {name} -> {spec.status} passed={rec['passed']}", log_path)
    return out


def reeval_session_screened(new_names: list[str], log_path: Path | None = None) -> dict:
    """Production-gate only the factors this session newly screened (not the whole pile)."""
    ops = LibraryOps()
    names = []
    have = {str(f.get("name")): f for f in FactorRegistry().list_factors()}
    for n in new_names:
        item = have.get(n)
        if item and item.get("status") == "screened":
            names.append(n)
    _safe_print(f"[quality-mine] production reeval n={len(names)} {names[:20]}", log_path)
    if not names:
        cap = ops.cap_usable_per_mechanism()
        return {"promoted": [], "held_screened": [], "errors": [], "mech_capped": cap.get("demoted") or []}
    result = ops.promote_screened(names=names)
    _safe_print(
        f"[quality-mine] promoted={result.get('promoted')} held={len(result.get('held_screened') or [])} "
        f"errors={len(result.get('errors') or [])}",
        log_path,
    )
    return result


def main() -> None:
    duration_s = int(sys.argv[1]) if len(sys.argv) > 1 else 7200
    tag = str(sys.argv[2]) if len(sys.argv) > 2 else ("three_hour" if duration_s >= 10800 else "two_hour")
    cfg = get_project_config()
    out_dir = cfg.path("runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / f"{tag}_mine_status.json"
    log_path = out_dir / f"{tag}_mine.log"
    inventory_path = out_dir / "usable_inventory.json"
    started = datetime.now(timezone.utc)
    t0 = time.time()
    deadline = t0 + duration_s
    baseline = _status_counts()
    baseline_names = {str(f.get("name")) for f in FactorRegistry().list_factors()}
    rounds: list[dict] = []
    loop = FactorLoop()

    payload: dict = {
        "started_at": started.isoformat(),
        "deadline_s": duration_s,
        "tag": tag,
        "baseline": baseline,
        "rounds": rounds,
        "status": "running",
        "produced_session": [],
        "restored": [],
        "production_reeval": {},
        "io_errors": 0,
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _safe_print(f"[quality-mine] start baseline={baseline} duration={duration_s}s", log_path)

    try:
        payload["restored"] = restore_known_candidates(log_path)
        payload["after_restore"] = _status_counts()
        status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        payload["restore_error"] = str(e)
        payload["restore_traceback"] = traceback.format_exc()
        _safe_print(f"[quality-mine] restore ERROR {e}", log_path)

    n = 0
    while True:
        remaining = deadline - time.time()
        if remaining < 30:
            _safe_print("[quality-mine] time budget exhausted", log_path)
            break
        n += 1
        packed = graph_rounds_for_budget(remaining, est_s=90, max_n=10)
        _safe_print(f"[quality-mine] round {n} remaining={remaining:.0f}s packed={packed}", log_path)
        try:
            result = loop.run(
                rounds=packed,
                batch_size=8,
                gate_name="research",
                resume=True,
            )
        except OSError as e:
            payload["io_errors"] = int(payload.get("io_errors") or 0) + 1
            _safe_print(f"[quality-mine] OSError round {n}: {e}", log_path)
            time.sleep(2)
            continue
        except Exception as e:
            payload["status"] = "error"
            payload["error"] = str(e)
            payload["traceback"] = traceback.format_exc()
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            payload["rounds_done"] = n
            payload["elapsed_s"] = round(time.time() - t0, 1)
            payload["current"] = _status_counts()
            status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            _safe_print(f"[quality-mine] ERROR {e}", log_path)
            raise

        rec = {
            "n": n,
            "elapsed_s": round(time.time() - t0, 1),
            "remaining_s": round(deadline - time.time(), 1),
            "produced": result.get("produced") or [],
            "saved_total": result.get("saved_total") or [],
            "production_promo": result.get("production_promo") or {},
            "status": result.get("status"),
            "run_dir": result.get("run_dir"),
            "mechanism_hits": result.get("mechanism_hits") or {},
            "llm_ratio": result.get("llm_ratio"),
        }
        rounds.append(rec)
        produced_names = [
            p.get("name")
            for r in rounds
            for p in (r.get("produced") or [])
            if p.get("name")
        ]
        payload.update(
            {
                "status": "running",
                "rounds_done": n,
                "elapsed_s": round(time.time() - t0, 1),
                "current": _status_counts(),
                "produced_session": produced_names,
                "last_round": {
                    "n": n,
                    "status": rec["status"],
                    "produced": rec["produced"],
                    "promo": rec["production_promo"],
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        try:
            status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            payload["io_errors"] = int(payload.get("io_errors") or 0) + 1
            _safe_print(f"[quality-mine] status write failed: {e}", log_path)
        _safe_print(
            f"[quality-mine] round {n} done status={rec['status']} "
            f"produced={len(rec['produced'])} catalog={payload.get('current')}",
            log_path,
        )

    session_new = [
        str(f.get("name"))
        for f in FactorRegistry().list_factors()
        if str(f.get("name")) not in baseline_names
    ]
    payload["session_new_factors"] = session_new
    payload["status"] = "reeval"
    payload["finished_mine_at"] = datetime.now(timezone.utc).isoformat()
    payload["elapsed_s"] = round(time.time() - t0, 1)
    payload["rounds_done"] = n
    payload["current"] = _status_counts()
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    screened_new = [
        str(f.get("name"))
        for f in FactorRegistry().list_factors()
        if str(f.get("name")) in set(session_new) and f.get("status") == "screened"
    ]
    try:
        payload["production_reeval"] = reeval_session_screened(screened_new, log_path)
    except Exception as e:
        payload["production_reeval"] = {"error": str(e), "traceback": traceback.format_exc()}
        _safe_print(f"[quality-mine] reeval ERROR {e}", log_path)

    payload["status"] = "done"
    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["elapsed_s"] = round(time.time() - t0, 1)
    payload["current"] = _status_counts()
    payload["delta"] = {
        k: int(payload["current"].get(k, 0)) - int(baseline.get(k, 0))
        for k in sorted(set(baseline) | set(payload["current"]))
    }
    inv = write_usable_inventory(inventory_path)
    payload["usable_inventory"] = {
        "path": str(inventory_path),
        "n_usable": inv.get("n_usable"),
        "mechanisms": inv.get("mechanisms"),
        "names": [r.get("name") for r in inv.get("factors") or []],
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _safe_print(
        f"[quality-mine] DONE rounds={n} elapsed={payload['elapsed_s']}s "
        f"delta={payload['delta']} usable={payload['usable_inventory']}",
        log_path,
    )
    _safe_print("QUALITY_MINE_DONE", log_path)


if __name__ == "__main__":
    main()
