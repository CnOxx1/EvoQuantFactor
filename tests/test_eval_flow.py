import numpy as np
import pandas as pd

from qfactor.eval.service import panel_coverage
from qfactor.eval.ic import rank_ic, summarize_ic, yearly_ic_sign_consistency
from qfactor.eval.oos import cost_layered, holdout_oos, walk_forward_after, walk_forward_ic
from qfactor.eval.timing import apply_trade_lag, forward_close_returns, slice_eval_index


def test_panel_coverage_uses_pit_members_not_union():
    idx = ["20240102", "20240103"]
    panel = pd.DataFrame(
        [[1.0, np.nan, 2.0], [1.5, np.nan, 2.5]],
        index=idx,
        columns=["in_a", "out", "in_b"],
    )
    mask = pd.DataFrame(
        [[True, False, True], [True, False, True]],
        index=idx,
        columns=["in_a", "out", "in_b"],
    )
    assert abs(float(panel.notna().mean().mean()) - 2.0 / 3.0) < 1e-9
    assert panel_coverage(panel, mask) == 1.0
    panel.iloc[0, 0] = np.nan
    assert abs(panel_coverage(panel, mask) - 0.75) < 1e-9


def test_apply_trade_lag_shifts_signal():
    idx = pd.Index(["20240102", "20240103", "20240104"])
    cols = ["a", "b"]
    panel = pd.DataFrame([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], index=idx, columns=cols)
    lagged = apply_trade_lag(panel, 1)
    assert pd.isna(lagged.iloc[0, 0])
    assert lagged.iloc[1, 0] == 1.0
    assert lagged.iloc[2, 1] == 4.0


def test_apply_trade_lag_zero_is_identity():
    panel = pd.DataFrame([[1.0]])
    assert apply_trade_lag(panel, 0).equals(panel)


def test_forward_close_returns_horizon():
    close = pd.DataFrame({"a": [10.0, 11.0, 12.0, 13.0]})
    fwd = forward_close_returns(close, 2)
    assert abs(float(fwd.iloc[0, 0]) - 0.2) < 1e-9
    assert pd.isna(fwd.iloc[-1, 0])


def _panel(n_days: int, n_names: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = [f"{i:04d}" for i in range(n_days)]
    cols = [f"s{j}" for j in range(n_names)]
    factor = pd.DataFrame(rng.normal(size=(n_days, n_names)), index=idx, columns=cols)
    return factor, idx, cols, rng


def test_walk_forward_uses_train_orientation():
    n_days, n_names = 200, 10
    factor, idx, cols, rng = _panel(n_days, n_names, seed=1)
    noise = pd.DataFrame(rng.normal(scale=0.05, size=(n_days, n_names)), index=idx, columns=cols)
    fwd = factor + noise
    fwd.iloc[100:] = -factor.iloc[100:] + noise.iloc[100:]

    oos = walk_forward_ic(factor, fwd, n_folds=4, min_days=40)
    assert oos["n_folds"] >= 2
    assert oos["folds"][0]["orientation"] == 1
    assert "period_stability" in oos
    assert oos["period_stability"]["n_folds"] >= 2
    later = [f["rank_ic_mean"] for f in oos["folds"][1:]]
    assert later and float(np.mean(later)) < oos["folds"][0]["rank_ic_mean"]


def test_rank_ic_matches_row_loop():
    factor, idx, cols, rng = _panel(40, 8, seed=2)
    fwd = factor + pd.DataFrame(rng.normal(scale=0.1, size=factor.shape), index=idx, columns=cols)
    got = rank_ic(factor, fwd, min_obs=5)
    expected = []
    dates = []
    for dt in factor.index:
        x = factor.loc[dt]
        y = fwd.loc[dt]
        mask = x.notna() & y.notna()
        if mask.sum() < 5:
            continue
        expected.append(x[mask].rank().corr(y[mask].rank()))
        dates.append(dt)
    exp = pd.Series(expected, index=dates)
    aligned = got.reindex(exp.index)
    assert len(aligned) == len(exp)
    assert float(np.nanmax(np.abs(aligned.to_numpy() - exp.to_numpy()))) < 1e-9


def test_summarize_ic_annualizes():
    ic = pd.Series([0.02, 0.04, 0.00, 0.02])
    s = summarize_ic(ic)
    assert s["icir_ann"] == s["icir"] * (252 ** 0.5)
    assert s["n"] == 4
    assert s["icir_nw"] == s["icir"]
    assert s["n_independent"] == 4


def test_newey_west_icir_shrinks_when_autocorrelated():
    from qfactor.eval.ic import newey_west_variance

    rng = np.random.default_rng(0)
    e = rng.normal(scale=0.05, size=200)
    ic = pd.Series(np.convolve(e, np.ones(5) / 5, mode="valid") + 0.03)
    naive = summarize_ic(ic, nw_lags=0)
    nw = summarize_ic(ic, nw_lags=4)
    assert nw["icir_nw"] < naive["icir"]
    assert nw["n_independent"] == round(len(ic) / 5)
    assert newey_west_variance(ic.to_numpy(), 4) > newey_west_variance(ic.to_numpy(), 0)


def test_drop_tail_trims_horizon_leak():
    from qfactor.eval.timing import drop_tail

    idx = pd.Index(["20251224", "20251225", "20251226", "20251229", "20251230", "20251231"])
    panel = pd.DataFrame({"a": range(6)}, index=idx)
    out = drop_tail(panel, 5)
    assert list(out.index.astype(str)) == ["20251224"]
    assert drop_tail(panel, 0).equals(panel)
    assert drop_tail(panel, 10).empty


def test_yearly_consistency_rejects_mixed_signs():
    idx = pd.Index(["20240102", "20250102", "20260102"])
    ic = pd.Series([-0.04, 0.03, 0.02], index=idx)
    out = yearly_ic_sign_consistency(ic, min_years=2)
    assert out["pos_years"] == 2
    assert out["neg_years"] == 1
    assert out["consistent"] is False
    assert out["dominant_years"] == 2


def test_yearly_consistency_all_same_sign():
    idx = pd.Index(["20240102", "20250102", "20260102"])
    ic = pd.Series([0.01, 0.02, 0.03], index=idx)
    out = yearly_ic_sign_consistency(ic, min_years=2)
    assert out["consistent"] is True


def test_cost_layered_is_one_day_units():
    layered = {"long_short": 0.001, "q1": 0.0, "q5": 0.001}
    out = cost_layered(layered, daily_turnover=0.5, cost_bps=10)
    # 0.5 * 10 / 10000 = 0.0005
    assert abs(out["cost_drag"] - 0.0005) < 1e-12
    assert abs(out["long_short_cost_adj"] - 0.0005) < 1e-12


def test_cost_layered_converts_horizon_to_daily():
    layered = {"long_short": 0.005, "q1": 0.0, "q5": 0.005}
    out = cost_layered(layered, daily_turnover=0.5, cost_bps=10, horizon=5)
    assert abs(out["long_short_daily"] - 0.001) < 1e-12
    assert abs(out["cost_drag"] - 0.0005) < 1e-12
    assert abs(out["long_short_cost_adj"] - 0.0005) < 1e-12


def test_neutralize_groups_demeans_within_industry():
    from qfactor.factor.transforms import neutralize_groups

    idx = ["d1", "d2"]
    cols = ["a", "b", "c", "d"]
    panel = pd.DataFrame(
        [[1.0, 3.0, 10.0, 14.0], [2.0, 4.0, 20.0, 24.0]],
        index=idx,
        columns=cols,
    )
    groups = pd.Series({"a": "x", "b": "x", "c": "y", "d": "y"})
    out = neutralize_groups(panel, groups)
    assert abs(float(out.loc["d1", ["a", "b"]].mean())) < 1e-12
    assert abs(float(out.loc["d1", ["c", "d"]].mean())) < 1e-12
    assert abs(float(out.loc["d1", "a"] - (-1.0))) < 1e-12
    assert abs(float(out.loc["d1", "c"] - (-2.0))) < 1e-12


def test_neutralize_numeric_residuals_on_size():
    from qfactor.factor.transforms import neutralize_numeric

    idx = ["d1", "d2"]
    cols = ["a", "b", "c", "d"]
    expo = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]],
        index=idx,
        columns=cols,
    )
    # y = 10 + 2*x exactly on d1
    panel = 10.0 + 2.0 * expo
    out = neutralize_numeric(panel, expo, min_obs=3)
    assert float(np.nanmax(np.abs(out.loc["d1"].to_numpy()))) < 1e-9


def test_slice_eval_index_train_holdout():
    idx = pd.Index(["20241231", "20250102", "20260105"])
    train = slice_eval_index(idx, "20251231", "train")
    hold = slice_eval_index(idx, "20251231", "holdout")
    assert list(train.astype(str)) == ["20241231", "20250102"]
    assert list(hold.astype(str)) == ["20260105"]
    assert list(slice_eval_index(idx, None, "full").astype(str)) == list(idx.astype(str))


def test_apply_signal_hold_smooths():
    from qfactor.eval.timing import apply_signal_hold

    panel = pd.DataFrame({"a": [1.0, 3.0, 5.0, 7.0, 9.0]})
    out = apply_signal_hold(panel, 3)
    assert abs(float(out.iloc[2, 0]) - 3.0) < 1e-12
    assert apply_signal_hold(panel, 1).equals(panel)


def test_holdout_oos_splits_two_folds():
    ic = pd.Series(
        [0.02] * 80 + [0.04] * 50 + [-0.03] * 50,
        index=[f"{i:04d}" for i in range(180)],
    )
    oos = holdout_oos(ic, after="0079", orientation=1, min_days=40, n_folds=2)
    assert oos["n_folds"] == 2
    assert oos["pos_folds"] == 1
    assert oos["oos_min_fold_ic"] < 0
    assert oos["oos_ic_mean"] > -0.01
    assert str(oos["folds"][0]["start"]) > "0079"


def test_walk_forward_after_uses_pre_cut_orientation():
    n_days, n_names = 200, 10
    factor, idx, cols, rng = _panel(n_days, n_names, seed=3)
    noise = pd.DataFrame(rng.normal(scale=0.05, size=(n_days, n_names)), index=idx, columns=cols)
    fwd = factor + noise
    ic = rank_ic(factor, fwd)
    after = str(idx[120])
    oos = walk_forward_after(ic, after=after, n_folds=2, min_days=30, orientation=1)
    assert oos["n_folds"] >= 1
    assert all(f["orientation"] == 1 for f in oos["folds"])
    assert str(oos["folds"][0]["start"]) > after


def test_residualize_on_peers_removes_span():
    from qfactor.factor.transforms import residualize_on_peers

    idx = ["d1", "d2", "d3"]
    cols = ["a", "b", "c", "d"]
    peer = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]],
        index=idx,
        columns=cols,
    )
    panel = 5.0 + 3.0 * peer
    out = residualize_on_peers(panel, {"p": peer}, min_obs=3)
    assert float(np.nanmax(np.abs(out.to_numpy()))) < 1e-8


def test_winsorize_clips_row_tails():
    from qfactor.factor.transforms import winsorize

    panel = pd.DataFrame([[0.0, 1.0, 2.0, 3.0, 100.0]])
    out = winsorize(panel, lower=0.2, upper=0.8)
    assert float(out.iloc[0, -1]) < 100.0
    assert float(out.iloc[0, 0]) >= 0.0


def test_monotonic_score_is_spearman():
    from qfactor.eval.layered import _spearman_mono

    assert abs(_spearman_mono([0.01, 0.02, 0.03, 0.04, 0.05]) - 1.0) < 1e-9
    assert _spearman_mono([0.05, 0.04, 0.03, 0.02, 0.01]) < 0
    noisy = _spearman_mono([0.01, 0.03, 0.02, 0.04, 0.05])
    assert 0.75 < noisy < 1.0
