import pandas as pd

from qfactor.eval.trading import simulate_non_overlapping_long_short


def test_non_overlapping_execution_ledger_applies_t1_costs_and_capacity():
    dates = pd.date_range("2024-01-01", periods=20, freq="B").strftime("%Y%m%d")
    codes = ["A", "B", "C", "D"]
    signal = pd.DataFrame([[1.0, 2.0, 3.0, 4.0]] * len(dates), index=dates, columns=codes)
    open_px = pd.DataFrame(100.0, index=dates, columns=codes)
    pre_close = pd.DataFrame(100.0, index=dates, columns=codes)
    close_px = pd.DataFrame([[90.0, 95.0, 105.0, 110.0]] * len(dates), index=dates, columns=codes)
    amount = pd.DataFrame(1_000_000.0, index=dates, columns=codes)
    is_st = pd.DataFrame(False, index=dates, columns=codes)

    out = simulate_non_overlapping_long_short(
        signal,
        open_px,
        close_px,
        pre_close,
        amount,
        is_st=is_st,
        trade_lag=1,
        hold_days=5,
        quantiles=2,
        min_names_per_leg=2,
        cost_bps=10.0,
        adv_participation=0.05,
    )

    assert out["n_filled"] > 0
    assert out["n_rebalances"] == out["n_filled"]
    assert out["trades"][0]["entry_date"] == dates[1]
    assert out["trades"][0]["exit_date"] == dates[6]
    assert out["net_long_short_mean"] < out["gross_long_short_mean"]
    assert out["capacity_per_name_median"] == 50_000.0
    assert out["mask"]["has_st_mask"] is True


def test_execution_ledger_discloses_missing_st_mask():
    dates = pd.date_range("2024-01-01", periods=10, freq="B").strftime("%Y%m%d")
    codes = ["A", "B", "C", "D"]
    panel = pd.DataFrame([[1.0, 2.0, 3.0, 4.0]] * len(dates), index=dates, columns=codes)
    out = simulate_non_overlapping_long_short(
        panel,
        pd.DataFrame(100.0, index=dates, columns=codes),
        pd.DataFrame(100.0, index=dates, columns=codes),
        pd.DataFrame(100.0, index=dates, columns=codes),
        trade_lag=1,
        hold_days=3,
        quantiles=2,
        min_names_per_leg=2,
    )
    assert "missing_point_in_time_st_mask" in out["limitations"]
    assert "missing_adv_capacity_input" in out["limitations"]
