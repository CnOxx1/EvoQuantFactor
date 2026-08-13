from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qfactor.eval.ic import rank_ic, summarize_ic


def period_stability_ic(
    ic: pd.Series,
    n_folds: int = 4,
    min_days: int = 40,
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
        s = summarize_ic(part)
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


def holdout_oos(
    ic: pd.Series,
    after: str,
    orientation: int = 1,
    min_days: int = 40,
) -> dict[str, Any]:
    """Treat the entire window after `after` as one OOS fold (frozen sign).

    Short holdouts should not be sliced into extra folds — that just adds noise
    and invites lowering pos_folds to 1-of-2.
    """
    empty = {
        "folds": [],
        "oos_ic_mean": 0.0,
        "oos_icir": 0.0,
        "n_folds": 0,
        "pos_folds": 0,
        "period_stability": period_stability_ic(ic, n_folds=2, min_days=min_days),
    }
    if ic.empty:
        return empty
    hold = ic.loc[ic.index.astype(str) > str(after)] * int(orientation)
    if hold.empty or len(hold) < min_days:
        return empty
    s = summarize_ic(hold)
    mean = float(s["rank_ic_mean"])
    return {
        "folds": [
            {
                "fold": 1,
                "start": str(hold.index[0]),
                "end": str(hold.index[-1]),
                "orientation": int(orientation),
                **s,
            }
        ],
        "oos_ic_mean": mean,
        "oos_icir": float(s["icir"]),
        "n_folds": 1,
        "pos_folds": 1 if mean > 0 else 0,
        "period_stability": period_stability_ic(hold, n_folds=2, min_days=min_days),
    }


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
