import pandas as pd
import pytest
from types import SimpleNamespace

from qfactor.eval.oos import holdout_window
from qfactor.eval.partitions import EvaluationPartitions
from qfactor.eval.service import EvalService


def test_partitions_require_strictly_non_overlapping_windows():
    p = EvaluationPartitions(
        discovery_start="20190101",
        discovery_end="20231231",
        selection_start="20240101",
        selection_end="20241231",
        sealed_start="20250101",
        sealed_end="20251231",
    )
    assert p.as_dict()["sealed_end"] == "20251231"

    with pytest.raises(ValueError, match="strictly after discovery"):
        EvaluationPartitions(
            discovery_start="20190101",
            discovery_end="20231231",
            selection_start="20231231",
            selection_end="20241231",
        ).validate()


def test_holdout_window_uses_only_explicit_sealed_dates():
    dates = pd.date_range("2024-01-01", periods=42, freq="D").strftime("%Y%m%d")
    ic = pd.Series([1.0] * 20 + [2.0] * 20 + [100.0, 100.0], index=dates)
    out = holdout_window(
        ic,
        start=str(dates[0]),
        end=str(dates[39]),
        orientation=1,
        min_days=40,
        n_folds=2,
    )
    assert out["oos_ic_mean"] == 1.5
    assert out["folds"][0]["start"] == str(dates[0])
    assert out["folds"][-1]["end"] == str(dates[39])


def test_production_evaluation_cannot_read_sealed_window():
    dates = pd.date_range("2020-01-01", periods=90, freq="D").strftime("%Y%m%d")
    names = [f"s{i}" for i in range(40)]
    panel = pd.DataFrame(
        [[float(i + j) for j in range(40)] for i in range(90)],
        index=dates,
        columns=names,
    )
    cfg = SimpleNamespace(
        eval={
            "default": {},
            "production": {
                "min_rank_ic_mean": -1,
                "min_abs_rank_ic_mean": 0,
                "min_holdout_icir": 0,
                "icir_mode": "newey_west",
                "min_coverage": 0,
                "max_corr_existing": 1,
                "min_years_consistent": 0,
                "max_daily_turnover": 9,
                "min_layered_monotonic_score": 0,
                "require_no_lookahead": True,
            },
            "eval": {
                "trade_lag": 1,
                "forward_horizon": 1,
                "signal_hold_days": 1,
                "n_quantiles": 5,
                "min_obs_per_day": 5,
                "oos_min_days": 10,
                "partitions": {
                    "discovery_start": dates[0],
                    "discovery_end": dates[29],
                    "selection_start": dates[30],
                    "selection_end": dates[59],
                    "sealed_start": dates[60],
                    "sealed_end": dates[89],
                },
            },
        },
        project={},
    )
    svc = object.__new__(EvalService)
    svc.cfg = cfg
    svc._peer_cache = {}
    svc._industry_map = None
    svc.data = SimpleNamespace(
        status=lambda: {"meta": {}},
        data_version=lambda: "data-v1",
    )
    svc._prepare_eval_panel = lambda value: (value, [])
    svc._forward_returns = lambda _horizon: panel
    svc._peer_panels = lambda *_args, **_kwargs: {}

    report = svc.evaluate_panel(panel, "f", gate_name="production")

    assert report["metrics"]["eval_split"] == "selection"
    assert report["metrics"]["eval_start"] >= dates[30]
    assert report["metrics"]["eval_end"] <= dates[59]
    assert report["metrics"]["eval_end"] < dates[60]
