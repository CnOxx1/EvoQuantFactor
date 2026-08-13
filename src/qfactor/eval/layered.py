from __future__ import annotations

import numpy as np
import pandas as pd

from qfactor.eval.ic import _align_panels, _nanmean_axis1, cs_rank


def _spearman_mono(series: list[float]) -> float:
    """Signed Spearman corr of quantile index vs quantile mean return."""
    y = np.asarray(series, dtype=float)
    n = int(y.size)
    if n < 3 or not np.isfinite(y).all() or abs(float(y[-1] - y[0])) < 1e-12:
        return 0.0
    x = np.arange(1, n + 1, dtype=float)
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    den = float(np.sqrt((rx * rx).sum() * (ry * ry).sum()))
    if den < 1e-12:
        return 0.0
    return float((rx * ry).sum() / den)


def layered_returns(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    n_quantiles: int = 5,
    min_obs: int | None = None,
) -> dict:
    min_obs = n_quantiles * 3 if min_obs is None else int(min_obs)
    idx, _, f, r = _align_panels(factor, forward_ret)
    empty = {f"q{q}": 0.0 for q in range(1, n_quantiles + 1)}
    empty["long_short"] = 0.0
    empty["monotonic_score"] = 0.0
    empty["monotonic_steps"] = 0.0
    if len(idx) == 0 or f.size == 0:
        return empty

    valid = np.isfinite(f) & np.isfinite(r)
    n = valid.sum(axis=1)
    ranks = cs_rank(np.where(valid, f, np.nan))
    denom = np.maximum(n.astype(float), 1.0)
    pct = ranks / denom[:, None]
    q = np.floor(pct * n_quantiles) + 1.0
    q = np.clip(q, 1.0, float(n_quantiles))
    q = np.where(valid & (n[:, None] >= min_obs), q, np.nan)

    means: dict[str, float] = {}
    series: list[float] = []
    for qi in range(1, n_quantiles + 1):
        rr = np.where(q == qi, r, np.nan)
        row_mean = _nanmean_axis1(rr)
        finite = row_mean[np.isfinite(row_mean)]
        val = float(finite.mean()) if finite.size else 0.0
        means[f"q{qi}"] = val
        series.append(val)

    if abs(series[-1] - series[0]) < 1e-12:
        steps = 0.0
    else:
        direction = np.sign(series[-1] - series[0])
        steps = float(np.mean(np.sign(np.diff(series)) == direction))
    means["long_short"] = series[-1] - series[0]
    means["monotonic_steps"] = steps
    means["monotonic_score"] = _spearman_mono(series)
    return means


def flip_layered(layered: dict, n_quantiles: int = 5) -> dict:
    """Flip quantile labels after a full-sample sign orientation."""
    qs = [float(layered.get(f"q{i}", 0.0)) for i in range(1, n_quantiles + 1)]
    qs = qs[::-1]
    out = {f"q{i}": qs[i - 1] for i in range(1, n_quantiles + 1)}
    out["long_short"] = -float(layered.get("long_short", 0.0))
    if abs(qs[-1] - qs[0]) < 1e-12:
        steps = 0.0
    else:
        direction = np.sign(qs[-1] - qs[0])
        steps = float(np.mean(np.sign(np.diff(qs)) == direction))
    out["monotonic_steps"] = steps
    out["monotonic_score"] = _spearman_mono(qs)
    return out
