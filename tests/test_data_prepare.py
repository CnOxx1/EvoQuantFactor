from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qfactor.agent.supervisor import FactoryRuntime
from qfactor.data.prepare import (
    DataPrepareResult,
    DataPrepareService,
    data_prepare_settings,
    is_snapshot_universe_error,
)


PIT_ERROR = (
    "Point-in-time CSI100 constituents require a verified historical "
    "reconstitution provider. Configure data_sources.providers.universe; "
    "do not downgrade to snapshot for production."
)


class _Cfg:
    def __init__(self, prepare=None):
        self.project = {
            "timezone": "Asia/Shanghai",
            "data_prepare": prepare
            or {
                "start": "20200101",
                "end": "20260815",
                "source": "baostock",
                "allow_snapshot_universe": True,
                "refresh_if_incomplete": True,
                "require_research_contract": True,
                "require_window_coverage": True,
                "min_covering_names": 1,
            },
        }
        self.eval = {}


class _FakeData:
    def __init__(self, bars=None, meta=None, calendar_path=None):
        self.bars = bars if bars is not None else pd.DataFrame()
        self.meta = meta or {}
        self.sync_calls: list[dict] = []
        self.calendar_path = Path(calendar_path) if calendar_path else Path("/tmp/missing-cal")
        self.pit_error = PIT_ERROR
        self.fail_even_snapshot = False

    def status(self):
        return {
            "has_bars": not self.bars.empty,
            "data_version": self.meta.get("data_version"),
            "meta": self.meta,
        }

    def load_bars(self):
        if self.bars is None or self.bars.empty:
            raise FileNotFoundError("Missing bars")
        return self.bars

    def sync(self, start, end, source="baostock", allow_snapshot_universe=False, max_names=None):
        self.sync_calls.append(
            {
                "start": start,
                "end": end,
                "source": source,
                "allow_snapshot_universe": allow_snapshot_universe,
            }
        )
        if not allow_snapshot_universe:
            raise RuntimeError(self.pit_error)
        if self.fail_even_snapshot:
            raise RuntimeError("No daily bars downloaded")
        self.bars = pd.DataFrame(
            {
                "ts_code": ["AAA.SH", "AAA.SH"],
                "trade_date": ["20200102", "20260814"],
            }
        )
        self.meta = {
            "data_version": "synced-v1",
            "start": "20200102",
            "end": "20260814",
            "universe_mode": "snapshot",
            "circ_mv_source": "estimated",
        }
        return dict(self.meta)


def _panel(start: str, end: str, code: str = "AAA.SH") -> pd.DataFrame:
    return pd.DataFrame({"ts_code": [code, code], "trade_date": [start, end]})


def _ready_contracts(monkeypatch, *, research="passed"):
    import qfactor.data.prepare as prepare

    monkeypatch.setattr(
        prepare,
        "_factor_contracts",
        lambda cfg=None: {
            "data_version": "v1",
            "research": {"state": research, "issues": [] if research == "passed" else ["research_bars_missing"]},
            "candidate": {"state": "blocked", "issues": ["universe_not_pit"]},
            "release": {"state": "blocked", "issues": ["security_status_coverage_below_contract"]},
        },
    )


def test_snapshot_fallback_defaults_off():
    cfg = _Cfg(prepare={"start": "20160102", "end": "20260630", "source": "baostock"})
    settings = data_prepare_settings(cfg)
    assert settings["allow_snapshot_universe"] is False


def test_is_snapshot_universe_error_detects_pit_gap():
    assert is_snapshot_universe_error(RuntimeError(PIT_ERROR))
    assert not is_snapshot_universe_error(RuntimeError("No daily bars downloaded"))


def test_inspect_rejects_short_2024_slice():
    data = _FakeData(
        bars=_panel("20240102", "20260630"),
        meta={"start": "20240102", "end": "20260630", "data_version": "old"},
    )
    coverage = DataPrepareService(_Cfg(), data).inspect()
    assert coverage["window_covered"] is False
    assert coverage["n_covering"] == 0
    assert coverage["n_codes"] == 1


def test_inspect_accepts_names_spanning_target_window():
    data = _FakeData(bars=_panel("20200102", "20260814"))
    coverage = DataPrepareService(_Cfg(), data).inspect("20200101", "20260815")
    assert coverage["window_covered"] is True
    assert coverage["n_covering"] == 1
    assert coverage["covering_sample"] == ["AAA.SH"]


def test_sync_when_incomplete_tries_pit_then_snapshot(monkeypatch):
    _ready_contracts(monkeypatch)
    data = _FakeData(bars=_panel("20240102", "20260630"))
    result = DataPrepareService(_Cfg(), data).ensure_research_ready()
    assert [c["allow_snapshot_universe"] for c in data.sync_calls] == [False, True]
    assert result.synced is True
    assert result.used_snapshot_universe is True
    assert result.reason == "synced_snapshot"
    assert result.coverage["window_covered"] is True
    assert result.mining_allowed is True
    assert result.contracts["candidate"] == "blocked"


def test_skip_sync_when_window_already_covered(monkeypatch):
    _ready_contracts(monkeypatch)
    data = _FakeData(bars=_panel("20200102", "20260814"))
    result = DataPrepareService(_Cfg(), data).ensure_research_ready()
    assert data.sync_calls == []
    assert result.skipped_sync is True
    assert result.synced is False
    assert result.reason == "window_covered"
    assert result.mining_allowed is True


def test_inspect_only_does_not_download(monkeypatch):
    _ready_contracts(monkeypatch)
    data = _FakeData(bars=_panel("20240102", "20260630"))
    result = DataPrepareService(_Cfg(), data).ensure_research_ready(sync=False)
    assert data.sync_calls == []
    assert result.skipped_sync is True
    assert result.mining_allowed is False
    assert "window_incomplete" in (result.block_reason or "")


def test_mining_blocked_when_research_contract_fails(monkeypatch):
    _ready_contracts(monkeypatch, research="blocked")
    data = _FakeData(bars=_panel("20200102", "20260814"))
    result = DataPrepareService(_Cfg(), data).ensure_research_ready()
    assert result.coverage["window_covered"] is True
    assert result.mining_allowed is False
    assert "research_bars_missing" in (result.block_reason or "")


def test_pit_failure_without_snapshot_flag_does_not_fake_universe(monkeypatch):
    _ready_contracts(monkeypatch)
    cfg = _Cfg()
    cfg.project["data_prepare"]["allow_snapshot_universe"] = False
    data = _FakeData(bars=_panel("20240102", "20260630"))
    result = DataPrepareService(cfg, data).ensure_research_ready()
    assert data.sync_calls == [{"start": "20200101", "end": "20260815", "source": "baostock", "allow_snapshot_universe": False}]
    assert result.synced is False
    assert result.used_snapshot_universe is False
    assert result.reason == "sync_failed"
    assert result.mining_allowed is False
    assert "sync_failed" in (result.block_reason or "")


def test_supervisor_blocks_discovery_when_prepare_forbids(tmp_path: Path):
    runtime = object.__new__(FactoryRuntime)
    runtime.runtime_dir = tmp_path
    runtime.status_path = tmp_path / "status.json"
    runtime.events_path = tmp_path / "events.jsonl"
    runtime.stop_path = tmp_path / "STOP"
    runtime.data = SimpleNamespace(status=lambda: {"data_version": "v1", "meta": {}})
    runtime.ops = SimpleNamespace(
        refresh_production=lambda include_screened=False: {"kept_candidates": []},
        multifactor_inventory=lambda: {"n_eligible": 0},
        reconcile_state=lambda: {"state": "consistent", "n_drift": 0},
    )
    runtime.release = SimpleNamespace(export_active=lambda: {"n_active": 0})
    runtime.discovery_every = 1
    runtime.screened_every = 10
    runtime.llm_ratio = 0.0
    runtime._discovery_contract = lambda: {"state": "passed", "reason": ""}
    runtime._candidate_contract = lambda: {"state": "blocked", "reason": "universe_not_pit"}
    runtime.lifecycle_counts = lambda: {"total": 0, "screened": 0, "candidate": 0, "active_release": 0}

    result = runtime.run_cycle(
        1,
        data_prepare={
            "mining_allowed": False,
            "reason": "window_incomplete",
            "block_reason": "window_incomplete:0<50",
        },
    )
    assert result["actions"]["research_discovery"] == {
        "state": "blocked",
        "reason": "window_incomplete:0<50",
    }
    assert result["data_prepare"]["mining_allowed"] is False


def test_run_forever_prepares_before_first_cycle(tmp_path: Path):
    runtime = object.__new__(FactoryRuntime)
    runtime.runtime_dir = tmp_path
    runtime.status_path = tmp_path / "status.json"
    runtime.events_path = tmp_path / "events.jsonl"
    runtime.stop_path = tmp_path / "STOP"
    runtime.interval_seconds = 60
    calls = []

    class _Prep:
        def ensure_research_ready(self, *, sync=None):
            calls.append(("prepare", sync))
            return DataPrepareResult(
                mining_allowed=True,
                reason="window_covered",
                coverage={"window_covered": True},
            )

    def _cycle(cycle, data_prepare=None):
        calls.append(("cycle", cycle, data_prepare["mining_allowed"] if data_prepare else None))
        runtime.stop_path.write_text("stop\n", encoding="utf-8")
        return {"cycle": cycle}

    runtime.prepare = _Prep()
    runtime.run_cycle = _cycle
    assert runtime.run_forever(start_cycle=12) == 0
    assert calls[0] == ("prepare", None)
    assert calls[1][0] == "prepare"
    assert calls[2] == ("cycle", 12, True)
