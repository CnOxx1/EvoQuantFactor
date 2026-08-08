"""Manual collect must not block the HTTP caller."""

from __future__ import annotations

import time
from unittest.mock import patch

from factor_backend.services.report_ingest import collector as coll


def test_start_collect_once_async_returns_immediately_and_dedupes():
    coll._set_status(running=False, last_finished_at=None, last_error=None)
    coll._manual_thread = None

    def _slow_run(*_a, **_k):
        time.sleep(0.35)
        coll._set_status(running=False, last_finished_at="done", last_added=1)
        return coll.get_collector_status()

    with patch.object(coll, "run_collect_once", side_effect=_slow_run):
        t0 = time.monotonic()
        first = coll.start_collect_once_async()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.2, f"should return immediately, took {elapsed:.3f}s"
        assert first.get("accepted") is True
        assert first.get("running") is True

        second = coll.start_collect_once_async()
        assert second.get("accepted") is False
        assert second.get("running") is True

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not coll.get_collector_status().get("running"):
                break
            time.sleep(0.05)
        assert coll.get_collector_status().get("running") is False
