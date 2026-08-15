from __future__ import annotations

import numpy as np
import pandas as pd


def winsorize(panel: pd.DataFrame, lower: float = 0.01, upper: float = 0.99) -> pd.DataFrame:
    """Cross-sectional clip at row quantiles. Vectorized; ignores all-NaN rows."""
    arr = panel.to_numpy(dtype=float, copy=True)
    finite = np.isfinite(arr)
    n = finite.sum(axis=1)
    ok = n >= 3
    if ok.any():
        with np.errstate(invalid="ignore"):
            lo = np.nanquantile(arr[ok], lower, axis=1)
            hi = np.nanquantile(arr[ok], upper, axis=1)
        clipped = np.clip(arr[ok], lo[:, None], hi[:, None])
        out_ok = np.where(finite[ok], clipped, np.nan)
        arr[ok] = out_ok
    return pd.DataFrame(arr, index=panel.index, columns=panel.columns)


def zscore(panel: pd.DataFrame) -> pd.DataFrame:
    mu = panel.mean(axis=1)
    sd = panel.std(axis=1).replace(0, np.nan)
    return panel.sub(mu, axis=0).div(sd, axis=0)


def rank(panel: pd.DataFrame) -> pd.DataFrame:
    return panel.rank(axis=1, pct=True)


def neutralize_groups(panel: pd.DataFrame, groups: pd.Series | pd.DataFrame) -> pd.DataFrame:
    """Cross-sectionally demean within groups, supporting PIT group matrices.

    A Series remains supported for legacy/static classifications. A DataFrame must
    be date x security and is neutralized separately on every date, which is the
    required path for point-in-time industry classifications.
    """
    if isinstance(groups, pd.DataFrame):
        g = groups.reindex(index=panel.index, columns=panel.columns)
        # Public research archives may repeat a current industry snapshot over
        # every date. That is semantically identical to a Series grouping, but
        # the generic PIT path below would otherwise execute hundreds of pandas
        # Arrow-backed row assignments for every candidate and every peer.
        # Retain the date-by-date algorithm whenever any security's label moves.
        filled = g.ffill().bfill()
        if not filled.empty:
            static_groups = filled.iloc[0]
            unchanged = (g.isna() | g.eq(static_groups, axis="columns")).to_numpy(dtype=bool).all()
            if unchanged:
                return neutralize_groups(panel, static_groups)
        out = panel.copy()
        for date in out.index:
            row_groups = g.loc[date]
            valid = row_groups.dropna()
            if int(valid.nunique()) < 2:
                continue
            row = out.loc[date].copy()
            for _name, names in valid.groupby(valid).groups.items():
                names = list(names)
                if len(names) < 2:
                    continue
                mean = row.loc[names].mean()
                row.loc[names] = row.loc[names] - mean
            out.loc[date] = row
        return out
    g = groups.reindex(panel.columns)
    valid = g.dropna()
    if int(valid.nunique()) < 2:
        return panel
    out = panel.copy()
    for _name, cols in valid.groupby(valid).groups.items():
        names = list(cols)
        if len(names) < 2:
            continue
        sub = out.loc[:, names]
        out.loc[:, names] = sub.sub(sub.mean(axis=1), axis=0)
    return out


def neutralize_numeric(
    panel: pd.DataFrame, expo: pd.DataFrame,     min_obs: int = 5
) -> pd.DataFrame:
    """Cross-sectional residual of `panel` on `expo` (intercept + slope), per date."""
    idx = panel.index.intersection(expo.index)
    cols = panel.columns.intersection(expo.columns)
    if len(idx) == 0 or len(cols) < 3:
        return panel
    y = panel.loc[idx, cols].to_numpy(dtype=float)
    x = expo.loc[idx, cols].to_numpy(dtype=float)
    mask = np.isfinite(y) & np.isfinite(x)
    n = mask.sum(axis=1)
    y0 = np.where(mask, y, np.nan)
    x0 = np.where(mask, x, np.nan)
    my = np.full(y.shape[0], np.nan)
    mx = np.full(x.shape[0], np.nan)
    ok = n >= min_obs
    if ok.any():
        my[ok] = np.nanmean(y0[ok], axis=1)
        mx[ok] = np.nanmean(x0[ok], axis=1)
    dy = np.where(mask, y0 - my[:, None], 0.0)
    dx = np.where(mask, x0 - mx[:, None], 0.0)
    var = (dx * dx).sum(axis=1)
    cov = (dy * dx).sum(axis=1)
    beta = np.zeros(y.shape[0])
    good = ok & (var > 1e-12)
    beta[good] = cov[good] / var[good]
    alpha = my - beta * mx
    resid = y - (alpha[:, None] + beta[:, None] * x)
    resid = np.where(mask & ok[:, None], resid, np.nan)
    out = panel.copy()
    out.loc[idx, cols] = resid
    return out


def residualize_on_peers(
    panel: pd.DataFrame,
    others: dict[str, pd.DataFrame],
    min_obs: int = 8,
) -> pd.DataFrame:
    """Cross-sectional residual of `panel` on library peer panels (intercept + peers)."""
    if not others:
        return panel
    idx = panel.index
    cols = panel.columns
    y = panel.to_numpy(dtype=float)
    xs = [
        peer.reindex(index=idx, columns=cols).to_numpy(dtype=float)
        for peer in others.values()
    ]
    t_count, n_names = y.shape
    k = len(xs)
    resid = np.full_like(y, np.nan)
    need = max(min_obs, k + 2)
    for t in range(t_count):
        yt = y[t]
        xmat = np.column_stack([np.ones(n_names)] + [x[t] for x in xs])
        mask = np.isfinite(yt) & np.isfinite(xmat).all(axis=1)
        if int(mask.sum()) < need:
            continue
        beta, *_ = np.linalg.lstsq(xmat[mask], yt[mask], rcond=None)
        pred = xmat @ beta
        row = np.full(n_names, np.nan)
        row[mask] = yt[mask] - pred[mask]
        resid[t] = row
    return pd.DataFrame(resid, index=idx, columns=cols)