from __future__ import annotations

import numpy as np
import pandas as pd

from qfactor.eval.ic import cs_rank


def _leg_turnover(memb: np.ndarray, valid: np.ndarray) -> float:
    if memb.shape[0] < 2:
        return 0.0
    prev, cur = memb[:-1], memb[1:]
    ok = valid[:-1].any(axis=1) & valid[1:].any(axis=1)
    if not ok.any():
        return 0.0
    inter = (prev & cur).sum(axis=1).astype(float)
    ncur = np.maximum(cur.sum(axis=1).astype(float), 1.0)
    turns = 1.0 - inter / ncur
    return float(turns[ok].mean())


def approx_daily_turnover(factor: pd.DataFrame, top_frac: float = 0.2) -> float:
    """
    Mean one-way turnover of long and short legs (top/bottom `top_frac`).

    The production cost gate uses this sum as the round-trip book turnover
    (long replacement + short replacement) in one-day units.
    """
    arr = factor.to_numpy(dtype=float)
    if arr.size == 0:
        return 0.0
    valid = np.isfinite(arr)
    n_valid = valid.sum(axis=1)
    n_leg = np.maximum(1, (n_valid * top_frac).astype(int))
    ranks = cs_rank(np.where(valid, arr, np.nan))
    # rank 1 = smallest. Long = highest values.
    long_cut = (n_valid - n_leg)[:, None]
    short_cut = n_leg[:, None]
    long = valid & (ranks > long_cut)
    short = valid & (ranks <= short_cut)
    t_long = _leg_turnover(long, valid)
    t_short = _leg_turnover(short, valid)
    return float(t_long + t_short)
