"""进程内轻量指标（供 /metrics；不做外部依赖）。"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_started_at = time.time()
_counters: dict[str, float] = {
    "jobs_claimed_total": 0,
    "jobs_succeeded_total": 0,
    "jobs_failed_total": 0,
    "jobs_cancelled_total": 0,
    "jobs_timed_out_total": 0,
    "llm_review_calls_total": 0,
    "llm_review_errors_total": 0,
    "llm_review_latency_ms_sum": 0,
    "llm_review_latency_ms_count": 0,
    "factor_library_write_errors_total": 0,
}


def incr(name: str, value: float = 1) -> None:
    with _lock:
        _counters[name] = float(_counters.get(name, 0)) + float(value)


def observe_ms(name_prefix: str, elapsed_ms: float) -> None:
    with _lock:
        _counters[f"{name_prefix}_ms_sum"] = float(_counters.get(f"{name_prefix}_ms_sum", 0)) + float(elapsed_ms)
        _counters[f"{name_prefix}_ms_count"] = float(_counters.get(f"{name_prefix}_ms_count", 0)) + 1


def snapshot() -> dict[str, Any]:
    with _lock:
        counters = {k: (int(v) if k.endswith("_total") or k.endswith("_count") else v) for k, v in _counters.items()}
    review_n = int(counters.get("llm_review_latency_ms_count") or 0)
    review_sum = float(counters.get("llm_review_latency_ms_sum") or 0.0)
    return {
        "uptime_sec": round(time.time() - _started_at, 1),
        "counters": counters,
        "derived": {
            "llm_review_latency_ms_avg": round(review_sum / review_n, 1) if review_n else None,
        },
    }


def reset_for_tests() -> None:
    global _started_at
    with _lock:
        for k in _counters:
            _counters[k] = 0
        _started_at = time.time()
