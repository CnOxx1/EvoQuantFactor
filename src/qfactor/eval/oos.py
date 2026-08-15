from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qfactor.eval.ic import rank_ic, summarize_ic


def period_stability_ic(
    ic: pd.Series,
    n_folds: int = 4,
    min_days: int = 40,
    nw_lags: int = 0,
) -> dict[str, Any]:
    """Split an IC series into contiguous folds (in-sample stability, not OOS)."""
    if ic.empty or len(ic) < min_days * 2:
        return {"folds": [], "ic_mean": 0.0, "icir": 0.0, "n_folds": 0, "pos_folds": 0}
    dates = list(ic.index)
    fold_size = max(min_days, len(dates) // n_folds)
    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else len(dates)
        if end - start < min_days // 2:
            continue
        part = ic.iloc[start:end]
        s = summarize_ic(part, nw_lags=nw_lags)
        folds.append(
            {"fold": i + 1, "start": str(dates[start]), "end": str(dates[end - 1]), **s}
        )
    if not folds:
        return {"folds": [], "ic_mean": 0.0, "icir": 0.0, "n_folds": 0, "pos_folds": 0}
    means = [f["rank_ic_mean"] for f in folds]
    mean = float(np.mean(means))
    std = float(np.std(means)) if len(means) > 1 else 0.0
    return {
        "folds": folds,
        "ic_mean": mean,
        "icir": float(mean / std) if std > 1e-12 else 0.0,
        "n_folds": len(folds),
        "pos_folds": int(sum(1 for m in means if m > 0)),
    }


def walk_forward_ic(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    n_folds: int = 4,
    min_days: int = 40,
    min_obs: int = 5,
    ic: pd.Series | None = None,
) -> dict[str, Any]:
    """
    Expanding walk-forward IC.

    Fold 0 is warmup. For each later fold, orientation (sign) is taken only from
    the past IC mean, then applied to the future fold. This is actual OOS,
    unlike contiguous splits of a full-sample IC series.
    """
    if ic is None:
        ic = rank_ic(factor, forward_ret, min_obs=min_obs)
    stability = period_stability_ic(ic, n_folds=n_folds, min_days=min_days)
    empty = {
        "folds": [],
        "oos_ic_mean": 0.0,
        "oos_icir": 0.0,
        "n_folds": 0,
        "pos_folds": 0,
        "period_stability": stability,
    }
    if ic.empty or len(ic) < min_days * 2:
        return empty

    dates = list(ic.index)
    fold_size = max(min_days, len(dates) // n_folds)
    folds: list[dict[str, Any]] = []
    for i in range(1, n_folds):
        test_start = i * fold_size
        test_end = (i + 1) * fold_size if i < n_folds - 1 else len(dates)
        if test_start >= len(dates):
            break
        train = ic.iloc[:test_start]
        test = ic.iloc[test_start:test_end]
        if len(train) < min_days // 2 or len(test) < min_days // 2:
            continue
        orient = 1 if float(train.mean()) >= 0 else -1
        oos = test * orient
        s = summarize_ic(oos)
        folds.append(
            {
                "fold": i,
                "start": str(dates[test_start]),
                "end": str(dates[test_end - 1]),
                "orientation": orient,
                "train_ic_mean": float(train.mean()),
                **s,
            }
        )
    if not folds:
        return empty
    means = [f["rank_ic_mean"] for f in folds]
    oos_mean = float(np.mean(means))
    oos_std = float(np.std(means)) if len(means) > 1 else 0.0
    return {
        "folds": folds,
        "oos_ic_mean": oos_mean,
        "oos_icir": float(oos_mean / oos_std) if oos_std > 1e-12 else 0.0,
        "n_folds": len(folds),
        "pos_folds": int(sum(1 for m in means if m > 0)),
        "period_stability": stability,
    }


def walk_forward_after(
    ic: pd.Series,
    after: str,
    n_folds: int = 2,
    min_days: int = 40,
    orientation: int | None = None,
) -> dict[str, Any]:
    """Expanding OOS on dates strictly after `after` (typically train_end).

    Orientation for each fold uses only IC before that fold's start (includes train).
    If `orientation` is set, the sign is frozen (definition freeze).
    """
    empty = {
        "folds": [],
        "oos_ic_mean": 0.0,
        "oos_icir": 0.0,
        "n_folds": 0,
        "pos_folds": 0,
        "period_stability": period_stability_ic(ic, n_folds=n_folds, min_days=min_days),
    }
    if ic.empty:
        return empty
    keys = ic.index.astype(str)
    hold = ic.loc[keys > str(after)]
    if hold.empty or len(hold) < min_days:
        return empty
    fold_size = max(min_days // 2, len(hold) // n_folds)
    folds: list[dict[str, Any]] = []
    hold_dates = list(hold.index)
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else len(hold_dates)
        if end - start < min_days // 2:
            continue
        test = hold.iloc[start:end]
        test_start = str(hold_dates[start])
        train = ic.loc[ic.index.astype(str) < test_start]
        if len(train) < min_days // 2:
            continue
        orient = int(orientation) if orientation is not None else (1 if float(train.mean()) >= 0 else -1)
        oos = test * orient
        s = summarize_ic(oos)
        folds.append(
            {
                "fold": i + 1,
                "start": str(hold_dates[start]),
                "end": str(hold_dates[end - 1]),
                "orientation": orient,
                "train_ic_mean": float(train.mean()) if len(train) else 0.0,
                **s,
            }
        )
    if not folds:
        return empty
    means = [f["rank_ic_mean"] for f in folds]
    oos_mean = float(np.mean(means))
    oos_std = float(np.std(means)) if len(means) > 1 else 0.0
    return {
        "folds": folds,
        "oos_ic_mean": oos_mean,
        "oos_icir": float(oos_mean / oos_std) if oos_std > 1e-12 else 0.0,
        "n_folds": len(folds),
        "pos_folds": int(sum(1 for m in means if m > 0)),
        "period_stability": period_stability_ic(hold, n_folds=n_folds, min_days=min_days),
    }


def holdout_window(
    ic: pd.Series,
    start: str,
    end: str,
    orientation: int = 1,
    min_days: int = 40,
    nw_lags: int = 0,
    n_folds: int = 2,
) -> dict[str, Any]:
    """Frozen-sign OOS statistics over an explicit inclusive date interval."""
    if str(start) > str(end):
        raise ValueError("sealed start must be <= sealed end")
    subset = ic.loc[(ic.index.astype(str) >= str(start)) & (ic.index.astype(str) <= str(end))]
    # Reuse holdout implementation with a synthetic predecessor below the interval.
    if subset.empty:
        return holdout_oos(pd.Series(dtype=float), after=start, orientation=orientation, min_days=min_days, nw_lags=nw_lags, n_folds=n_folds)
    return _holdout_stats(subset * int(orientation), orientation, min_days, nw_lags, n_folds)


def _holdout_stats(
    hold: pd.Series,
    orientation: int,
    min_days: int,
    nw_lags: int,
    n_folds: int,
) -> dict[str, Any]:
    """Compute fold statistics on a preselected, already oriented holdout IC series."""
    empty = {
        "folds": [],
        "oos_ic_mean": 0.0,
        "oos_icir": 0.0,
        "oos_min_fold_ic": 0.0,
        "n_folds": 0,
        "pos_folds": 0,
        "period_stability": period_stability_ic(
            hold, n_folds=2, min_days=min_days, nw_lags=nw_lags
        ),
    }
    folds_n = max(2, int(n_folds or 2))
    min_fold = max(20, int(min_days) // 2)
    if hold.empty or len(hold) < min_days or len(hold) < folds_n * min_fold:
        return empty
    fold_size = len(hold) // folds_n
    folds: list[dict[str, Any]] = []
    for i in range(folds_n):
        start = i * fold_size
        end = (i + 1) * fold_size if i < folds_n - 1 else len(hold)
        part = hold.iloc[start:end]
        if len(part) < min_fold:
            return empty
        s = summarize_ic(part, nw_lags=nw_lags)
        folds.append(
            {
                "fold": i + 1,
                "start": str(part.index[0]),
                "end": str(part.index[-1]),
                "orientation": int(orientation),
                **s,
            }
        )
    means = [float(f["rank_ic_mean"]) for f in folds]
    overall = summarize_ic(hold, nw_lags=nw_lags)
    return {
        "folds": folds,
        "oos_ic_mean": float(overall["rank_ic_mean"]),
        "oos_icir": float(overall.get("icir_nw") if nw_lags else overall["icir"]),
        "oos_min_fold_ic": float(min(means)),
        "n_folds": len(folds),
        "pos_folds": int(sum(1 for m in means if m > 0)),
        "period_stability": period_stability_ic(
            hold, n_folds=2, min_days=min_days, nw_lags=nw_lags
        ),
    }


def holdout_oos(
    ic: pd.Series,
    after: str,
    orientation: int = 1,
    min_days: int = 40,
    nw_lags: int = 0,
    n_folds: int = 2,
) -> dict[str, Any]:
    """Holdout after `after`, frozen sign, split into contiguous folds.

    Two folds catch a first-half spike that reverses later. A single window
    mean is not an extra OOS check beyond the production IC gate.
    """
    if ic.empty:
        return _holdout_stats(pd.Series(dtype=float), orientation, min_days, nw_lags, n_folds)
    hold = ic.loc[ic.index.astype(str) > str(after)] * int(orientation)
    return _holdout_stats(hold, orientation, min_days, nw_lags, n_folds)


def cost_layered(
    layered: dict[str, Any],
    daily_turnover: float,
    cost_bps: float,
    horizon: int = 1,
) -> dict[str, Any]:
    """Subtract one-day round-trip cost from a daily layered long-short return.

    If `layered` is an H-day cumulative long-short, pass horizon=H so it is
    converted to a one-day equivalent before subtracting daily cost.
    """
    h = max(1, int(horizon))
    ls = float(layered.get("long_short", 0.0))
    daily_ls = ls / h
    cost = daily_turnover * cost_bps / 10000.0
    out = dict(layered)
    out["long_short_daily"] = daily_ls
    out["long_short_cost_adj"] = daily_ls - cost
    out["cost_drag"] = cost
    out["cost_horizon"] = h
    return out


def cost_scenario_table(
    layered: dict[str, Any],
    daily_turnover: float,
    cost_bps_values: list[float],
    horizon: int = 1,
) -> list[dict[str, float]]:
    """Return diagnostic cost stress results without changing the gate scenario."""
    scenarios: list[dict[str, float]] = []
    for cost_bps in sorted({float(x) for x in cost_bps_values}):
        result = cost_layered(layered, daily_turnover, cost_bps, horizon=horizon)
        scenarios.append(
            {
                "cost_bps": cost_bps,
                "long_short_daily": float(result["long_short_daily"]),
                "cost_drag": float(result["cost_drag"]),
                "long_short_cost_adj": float(result["long_short_cost_adj"]),
            }
        )
    return scenarios
