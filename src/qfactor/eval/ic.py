from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _align_panels(
    a: pd.DataFrame, b: pd.DataFrame
) -> tuple[pd.Index, pd.Index, np.ndarray, np.ndarray]:
    idx = a.index.intersection(b.index)
    cols = a.columns.intersection(b.columns)
    return idx, cols, a.loc[idx, cols].to_numpy(dtype=float), b.loc[idx, cols].to_numpy(
        dtype=float
    )


def cs_rank(arr: np.ndarray) -> np.ndarray:
    """Cross-sectional average rank along axis 1; NaN stays NaN."""
    return pd.DataFrame(arr).rank(axis=1, method="average").to_numpy(dtype=float)


def _nanmean_axis1(arr: np.ndarray) -> np.ndarray:
    mask = np.isfinite(arr)
    n = mask.sum(axis=1)
    total = np.where(mask, arr, 0.0).sum(axis=1)
    out = np.full(arr.shape[0], np.nan)
    ok = n > 0
    out[ok] = total[ok] / n[ok]
    return out


def row_pearson(a: np.ndarray, b: np.ndarray, min_obs: int) -> np.ndarray:
    """Pearson correlation per row; NaN when fewer than min_obs overlapping points."""
    mask = np.isfinite(a) & np.isfinite(b)
    n = mask.sum(axis=1)
    a0 = np.where(mask, a, np.nan)
    b0 = np.where(mask, b, np.nan)
    ma = _nanmean_axis1(a0)
    mb = _nanmean_axis1(b0)
    da = np.where(mask, a0 - ma[:, None], 0.0)
    db = np.where(mask, b0 - mb[:, None], 0.0)
    cov = (da * db).sum(axis=1)
    va = (da * da).sum(axis=1)
    vb = (db * db).sum(axis=1)
    den = np.sqrt(va * vb)
    out = np.full(a.shape[0], np.nan)
    ok = (n >= min_obs) & (den > 1e-12)
    out[ok] = cov[ok] / den[ok]
    return out


def cs_spearman_series(
    a: pd.DataFrame, b: pd.DataFrame, min_obs: int = 5, name: str = "spearman"
) -> pd.Series:
    idx, _, xa, xb = _align_panels(a, b)
    if len(idx) == 0 or xa.size == 0:
        return pd.Series(dtype=float, name=name)
    corr = row_pearson(cs_rank(xa), cs_rank(xb), min_obs=min_obs)
    s = pd.Series(corr, index=idx, name=name)
    return s.dropna()


def rank_ic(
    factor: pd.DataFrame, forward_ret: pd.DataFrame, min_obs: int = 5
) -> pd.Series:
    return cs_spearman_series(factor, forward_ret, min_obs=min_obs, name="rank_ic")


def newey_west_variance(values: np.ndarray, lags: int) -> float:
    """Bartlett-kernel Newey-West variance of a 1-d series (not of the mean)."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    t = len(x)
    if t < 2:
        return 0.0
    u = x - x.mean()
    gamma0 = float(np.dot(u, u) / t)
    if lags <= 0:
        return max(gamma0, 0.0)
    acc = gamma0
    max_lag = min(int(lags), t - 1)
    for k in range(1, max_lag + 1):
        weight = 1.0 - k / (max_lag + 1)
        gamma_k = float(np.dot(u[k:], u[:-k]) / t)
        acc += 2.0 * weight * gamma_k
    return max(acc, 0.0)


def summarize_ic(ic: pd.Series, nw_lags: int = 0) -> dict:
    empty = {
        "rank_ic_mean": 0.0,
        "rank_ic_std": 0.0,
        "icir": 0.0,
        "icir_ann": 0.0,
        "icir_nw": 0.0,
        "n": 0,
        "n_independent": 0,
        "nw_lags": int(max(0, nw_lags)),
    }
    if ic.empty:
        return empty
    mean = float(ic.mean())
    std = float(ic.std(ddof=0)) if len(ic) > 1 else 0.0
    icir = float(mean / std) if std > 1e-12 else 0.0
    lags = int(max(0, nw_lags))
    var_nw = newey_west_variance(ic.to_numpy(dtype=float), lags)
    std_nw = float(np.sqrt(var_nw)) if var_nw > 0 else 0.0
    icir_nw = float(mean / std_nw) if std_nw > 1e-12 else 0.0
    n = int(len(ic))
    n_independent = max(1, int(round(n / (lags + 1)))) if lags else n
    return {
        "rank_ic_mean": mean,
        "rank_ic_std": std,
        "icir": icir,
        "icir_ann": float(icir * np.sqrt(TRADING_DAYS)),
        "icir_nw": icir_nw,
        "n": n,
        "n_independent": n_independent,
        "nw_lags": lags,
    }


def yearly_ic_sign_consistency(ic: pd.Series, min_years: int) -> dict:
    empty = {
        "years": {},
        "consistent": False,
        "pos_years": 0,
        "neg_years": 0,
        "n_years": 0,
        "dominant_years": 0,
    }
    if ic.empty:
        return empty
    years = pd.to_datetime(ic.index.astype(str)).year
    g = ic.groupby(years).mean()
    pos = int((g > 0).sum())
    neg = int((g < 0).sum())
    n_years = int(len(g))
    dominant = max(pos, neg)
    same_sign = (pos == 0 or neg == 0) and n_years >= min_years
    return {
        "years": {str(k): float(v) for k, v in g.items()},
        "consistent": bool(same_sign),
        "pos_years": pos,
        "neg_years": neg,
        "n_years": n_years,
        "dominant_years": dominant,
    }
