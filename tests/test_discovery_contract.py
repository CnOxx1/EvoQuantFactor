import pytest

from qfactor.agent.experiments import (
    candidate_contract_readiness,
    discovery_contract_readiness,
    require_candidate_contract,
    require_discovery_contract,
)


class _Cfg:
    def __init__(self, meta, partitions):
        self.project = {}
        self.eval = {
            "production": {
                "allowed_universe_modes": ["pit"],
                "allowed_circ_mv_sources": ["archive_daily_basic"],
                "min_daily_basic_coverage": 0.8,
            },
            "eval": {"partitions": partitions},
        }
        self._meta = meta


class _DataService:
    def __init__(self, cfg):
        self.cfg = cfg

    def status(self):
        return {"has_bars": True, "data_version": "data-v1", "meta": self.cfg._meta}


def _partitions():
    return {
        "discovery_start": "20190101",
        "discovery_end": "20231231",
        "selection_start": "20240101",
        "selection_end": "20241231",
        "sealed_start": "20250101",
        "sealed_end": "20251231",
    }


def test_candidate_requires_pit_vendor_cap_and_frozen_windows(monkeypatch):
    import qfactor.agent.experiments as experiments

    monkeypatch.setattr(experiments, "DataService", _DataService)
    cfg = _Cfg(
        {
            "start": "20190101",
            "end": "20251231",
            "universe_mode": "pit",
            "circ_mv_source": "archive_daily_basic",
            "daily_basic_coverage": 0.9,
        },
        _partitions(),
    )
    out = require_candidate_contract(cfg)
    assert out["state"] == "configured"
    assert out["sealed_oos"]["state"] == "configured"

    cfg._meta["universe_mode"] = "snapshot"
    with pytest.raises(RuntimeError, match="universe_not_pit"):
        require_candidate_contract(cfg)


def test_candidate_accepts_archive_vendor_cap_without_tushare(monkeypatch):
    import qfactor.agent.experiments as experiments

    monkeypatch.setattr(experiments, "DataService", _DataService)
    cfg = _Cfg(
        {
            "start": "20190101",
            "end": "20251231",
            "universe_mode": "pit",
            "circ_mv_source": "archive_daily_basic",
            "daily_basic_coverage": 0.9,
            "security_status_coverage": 0.99,
            "limit_price_coverage": 0.99,
            "adv_20d_coverage": 0.96,
            "corporate_action_coverage": 0.99,
            "industry_pit_coverage": 0.96,
            "risk_exposures_coverage": 0.96,
        },
        _partitions(),
    )
    out = require_candidate_contract(cfg)
    assert out["state"] == "configured"


def test_discovery_readiness_returns_structured_blockers(monkeypatch):
    import qfactor.agent.experiments as experiments

    monkeypatch.setattr(experiments, "DataService", _DataService)
    cfg = _Cfg(
        {
            "universe_mode": "snapshot",
            "circ_mv_source": "estimated",
            "daily_basic_coverage": 0.0,
        },
        {},
    )
    out = discovery_contract_readiness(cfg)
    assert out["state"] == "blocked"
    assert out["universe_mode"] == "snapshot"
    assert out["issues"] == ["discovery_partitions_unconfigured"]

    candidate = candidate_contract_readiness(cfg)
    assert candidate["coverage"]["daily_basic_coverage"] == 0.0
    assert candidate["issues"] == [
        "universe_not_pit",
        "circ_mv_not_verified_provider",
        "daily_basic_coverage_below_contract",
        "selection_partitions_unconfigured",
    ]


def test_research_does_not_require_execution_or_risk_data(monkeypatch):
    import qfactor.agent.experiments as experiments

    monkeypatch.setattr(experiments, "DataService", _DataService)
    cfg = _Cfg({"start": "20190101", "end": "20251231"}, _partitions())
    out = require_discovery_contract(cfg)
    assert out["state"] == "configured"
