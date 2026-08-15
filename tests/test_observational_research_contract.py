import pandas as pd
import pytest

from qfactor.agent.experiments import require_observational_research_contract


class _Cfg:
    def __init__(self):
        self.project = {
            "research_runtime": {
                "allow_observational_data": True,
                "allowed_universe_modes": ["snapshot"],
                "min_trading_days": 100,
                "min_securities": 2,
            }
        }
        self.eval = {
            "eval": {
                "partitions": {
                    "discovery_start": "20190101",
                    "discovery_end": "20231231",
                    "selection_start": "20240101",
                    "selection_end": "20241231",
                    "sealed_start": "20250101",
                    "sealed_end": "20251231",
                }
            }
        }
        self._meta = {
            "start": "20190101",
            "end": "20251231",
            "universe_mode": "snapshot",
            "limitations": ["latest_snapshot_membership"],
        }


class _DataService:
    def __init__(self, cfg):
        self.cfg = cfg

    def status(self):
        return {
            "has_bars": True,
            "has_universe": True,
            "data_version": "research-v1",
            "meta": self.cfg._meta,
        }

    def load_bars(self):
        dates = pd.date_range("2019-01-01", "2025-12-31", freq="B")
        return pd.DataFrame(
            {
                "trade_date": list(dates) * 2,
                "ts_code": ["000001.SZ"] * len(dates) + ["000002.SZ"] * len(dates),
            }
        )


def test_observational_contract_is_explicitly_research_only(monkeypatch):
    import qfactor.agent.experiments as experiments

    monkeypatch.setattr(experiments, "DataService", _DataService)
    cfg = _Cfg()

    out = require_observational_research_contract(cfg)

    assert out["contract_kind"] == "observational_research_only"
    assert out["production_eligible"] is False
    assert out["universe_mode"] == "snapshot"
    assert out["n_securities"] == 2
    assert out["sealed_oos"]["state"] == "configured"

    cfg._meta["universe_mode"] = "pit"
    with pytest.raises(RuntimeError, match="universe_mode_not_allowed:pit"):
        require_observational_research_contract(cfg)


def test_observational_contract_requires_explicit_operator_enablement(monkeypatch):
    import qfactor.agent.experiments as experiments

    monkeypatch.setattr(experiments, "DataService", _DataService)
    cfg = _Cfg()
    cfg.project["research_runtime"]["allow_observational_data"] = False

    with pytest.raises(RuntimeError, match="observational_research_disabled"):
        require_observational_research_contract(cfg)
