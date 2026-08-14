"""Run factor production until a wall-clock deadline (default 1 hour).

Usage:
  python runs/_hour_mine.py              # 3600s, hour_mine_*
  python runs/_hour_mine.py 7200 two_hour
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from qfactor.agent.loop import FactorLoop, graph_rounds_for_budget
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import get_project_config


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


def main() -> None:
    duration_s = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    tag = str(sys.argv[2]) if len(sys.argv) > 2 else ("two_hour" if duration_s >= 7200 else "hour")
    cfg = get_project_config()
    out_dir = cfg.path("runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / f"{tag}_mine_status.json"
    log_path = out_dir / f"{tag}_mine.log"
    started = datetime.now(timezone.utc)
    t0 = time.time()
    deadline = t0 + duration_s
    baseline = _status_counts()
    rounds: list[dict] = []
    loop = FactorLoop()

    payload = {
        "started_at": started.isoformat(),
        "deadline_s": duration_s,
        "baseline": baseline,
        "rounds": rounds,
        "status": "running",
        "produced_session": [],
        "saved_session": [],
        "production_promo": [],
        "io_errors": 0,
    }
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _safe_print(f"[hour-mine] start baseline={baseline} duration={duration_s}s", log_path)

    n = 0
    while True:
        remaining = deadline - time.time()
        if remaining < 20:
            _safe_print("[hour-mine] time budget exhausted", log_path)
            break
        n += 1
        _safe_print(f"[hour-mine] round {n} remaining={remaining:.0f}s", log_path)
        try:
            result = loop.run(
                rounds=graph_rounds_for_budget(remaining),
                batch_size=8,
                gate_name="research",
                resume=True,
            )
        except OSError as e:
            payload["io_errors"] = int(payload.get("io_errors") or 0) + 1
            _safe_print(f"[hour-mine] OSError round {n}: {e}", log_path)
            try:
                with (out_dir / "hour_mine_error.log").open("a", encoding="utf-8") as fh:
                    fh.write(traceback.format_exc() + "\n")
            except OSError:
                pass
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
            try:
                status_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            except OSError:
                pass
            _safe_print(f"[hour-mine] ERROR {e}", log_path)
            try:
                with (out_dir / "hour_mine_error.log").open("a", encoding="utf-8") as fh:
                    fh.write(payload["traceback"] + "\n")
            except OSError:
                pass
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
            status_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            payload["io_errors"] = int(payload.get("io_errors") or 0) + 1
            _safe_print(f"[hour-mine] status write failed: {e}", log_path)
        _safe_print(
            f"[hour-mine] round {n} done status={rec['status']} "
            f"produced={len(rec['produced'])} catalog={payload.get('current')}",
            log_path,
        )

    payload["status"] = "done"
    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    payload["elapsed_s"] = round(time.time() - t0, 1)
    payload["rounds_done"] = n
    payload["current"] = _status_counts()
    payload["delta"] = {
        k: int(payload["current"].get(k, 0)) - int(baseline.get(k, 0))
        for k in sorted(set(baseline) | set(payload["current"]))
    }
    status_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _safe_print(
        f"[hour-mine] DONE rounds={n} elapsed={payload['elapsed_s']}s "
        f"delta={payload['delta']}",
        log_path,
    )
    _safe_print("HOUR_MINE_DONE", log_path)


if __name__ == "__main__":
    main()
