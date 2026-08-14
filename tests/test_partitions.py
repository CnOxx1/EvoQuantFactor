import pandas as pd
import pytest

from qfactor.eval.oos import holdout_window
from qfactor.eval.partitions import EvaluationPartitions


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
