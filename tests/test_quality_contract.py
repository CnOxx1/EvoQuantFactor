import json
from pathlib import Path

from qfactor.agent.graph import _dsl_factor_code
from qfactor.eval.gate import apply_gate
from qfactor.factor.base import FactorSpec
from qfactor.factor.ops import LibraryOps
from qfactor.settings import get_project_config


def _production_metrics(**overrides):
    metrics = {
        "rank_ic_mean": 0.04,
        "train_rank_ic_mean": 0.03,
        "icir": 0.12,
        "icir_nw": 0.12,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 0.5,
        "years_consistent": True,
        "no_lookahead": True,
        "recent_rank_ic_mean": 0.03,
        "freeze_sign_ok": True,
        "oos_ic_mean": 0.04,
        "oos_min_fold_ic": 0.02,
        "oos_pos_folds": 2,
        "cost_adjusted_ls": 0.01,
        "resid_ic_mean": 0.02,
        "resid_icir_nw": 0.10,
        "universe_mode": "pit",
        "circ_mv_source": "tushare_daily_basic",
        "daily_basic_coverage": 0.9,
        "security_status_coverage": 1.0,
        "limit_price_coverage": 1.0,
        "adv_20d_coverage": 1.0,
        "corporate_action_coverage": 1.0,
        "industry_pit_coverage": 1.0,
        "risk_exposures_coverage": 1.0,
        "n_independent": 80,
    }
    metrics.update(overrides)
    return metrics


def test_live_production_contract_requires_data_quality():
    cfg = get_project_config()
    thresholds = dict(cfg.eval["production"])
    assert apply_gate(_production_metrics(), thresholds, mode="production")["status"] == "candidate"

    snapshot = apply_gate(
        _production_metrics(universe_mode="snapshot"), thresholds, mode="production"
    )
    assert snapshot["checks"]["universe"] is False
    assert snapshot["status"] == "reject"

    estimated = apply_gate(
        _production_metrics(circ_mv_source="estimated"), thresholds, mode="production"
    )
    assert estimated["checks"]["vendor_circ_mv"] is False
    assert estimated["status"] == "reject"

    too_short = apply_gate(
        _production_metrics(n_independent=22), thresholds, mode="production"
    )
    assert too_short["checks"]["independent_obs"] is False
    assert too_short["status"] == "reject"

    no_execution = apply_gate(
        _production_metrics(security_status_coverage=0.0), thresholds, mode="production"
    )
    assert no_execution["checks"]["security_status"] is False
    assert no_execution["status"] == "reject"


def test_research_soft_pass_respects_turnover_when_enabled():
    thresholds = {
        "min_rank_ic_mean": 0.01,
        "min_abs_rank_ic_mean": 0.01,
        "min_icir": 1.0,
        "min_coverage": 0.7,
        "max_corr_existing": 0.8,
        "min_layered_monotonic_score": 0.5,
        "max_daily_turnover": 1.5,
        "require_no_lookahead": True,
        "soft_require_turnover": True,
    }
    metrics = {
        "rank_ic_mean": 0.03,
        "icir": 0.08,
        "icir_ann": 1.3,
        "coverage": 0.8,
        "max_corr": 0.2,
        "monotonic_score": 0.8,
        "daily_turnover": 3.0,
        "years_consistent": False,
        "no_lookahead": True,
    }
    out = apply_gate(metrics, thresholds, mode="research")
    assert out["checks"]["turnover"] is False
    assert out["status"] == "reject"


def test_dsl_factor_code_escapes_untrusted_hypothesis():
    hypothesis = 'normal claim\n"""\nraise RuntimeError("injected")\n#'
    code = _dsl_factor_code(
        "safe_name",
        "ma(close,5)",
        "momentum",
        hypothesis,
    )
    compile(code, "generated_factor.py", "exec")
    assert "hypothesis=" in code
    assert "RuntimeError" in code


class _FakeRegistry:
    def __init__(self, root: Path):
        self.root = root

    def list_factors(self):
        return [{"name": "quality_factor", "status": "candidate"}]

    def factor_dir(self, name: str) -> Path:
        return self.root / name

    def load_spec(self, name: str) -> FactorSpec:
        return FactorSpec(
            name=name,
            mechanism="momentum",
            expression="ma(close,5)",
        )


class _FakeData:
    def data_version(self):
        return "data-v1"


class _FakeEvalService:
    def __init__(self, _cfg):
        self.data = _FakeData()


class _FakeDatabase:
    def list_releases(self, state=None):
        assert state == "active"
        return [
            {
                "release_id": "rel_quality",
                "name": "quality_factor",
                "state": "active",
                "data_version": "data-v1",
            }
        ]


def test_multifactor_inventory_exports_only_contract_compliant_factor(tmp_path, monkeypatch):
    factor_dir = tmp_path / "quality_factor"
    report_dir = factor_dir / "reports"
    report_dir.mkdir(parents=True)
    report = {
        "gate": {"mode": "production", "passed": True},
        "metrics": {
            "data_version": "data-v1",
            "universe_mode": "pit",
            "circ_mv_source": "tushare_daily_basic",
            "daily_basic_coverage": 0.9,
            "security_status_coverage": 1.0,
            "limit_price_coverage": 1.0,
            "adv_20d_coverage": 1.0,
            "corporate_action_coverage": 1.0,
            "industry_pit_coverage": 1.0,
            "risk_exposures_coverage": 1.0,
            "n_independent": 80,
            "train_rank_ic_mean": 0.03,
            "rank_ic_mean": 0.04,
            "icir_nw": 0.10,
            "resid_ic_mean": 0.02,
            "resid_icir_nw": 0.09,
            "oos_ic_mean": 0.02,
            "oos_min_fold_ic": 0.01,
            "coverage": 0.8,
            "daily_turnover": 0.5,
            "max_corr": 0.2,
            "cost_adjusted_ls": 0.01,
            "signal_hold_days": 5,
            "trade_lag": 1,
        },
    }
    (report_dir / "latest.json").write_text(json.dumps(report), encoding="utf-8")

    cfg = get_project_config()
    ops = object.__new__(LibraryOps)
    ops.cfg = cfg
    ops.registry = _FakeRegistry(tmp_path)
    monkeypatch.setattr("qfactor.factor.ops.EvalService", _FakeEvalService)
    monkeypatch.setattr("qfactor.factor.ops.Database", _FakeDatabase)

    inventory = ops.multifactor_inventory()
    assert inventory["n_eligible"] == 1
    assert inventory["n_excluded"] == 0
    assert inventory["factors"][0]["name"] == "quality_factor"
    assert inventory["factors"][0]["data_version"] == "data-v1"
    assert inventory["factors"][0]["release_id"] == "rel_quality"
