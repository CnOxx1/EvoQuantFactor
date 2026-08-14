from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _align_optional(
    frame: pd.DataFrame | None, index: pd.Index, columns: pd.Index
) -> pd.DataFrame | None:
    if frame is None:
        return None
    return frame.reindex(index=index, columns=columns)


def _coverage(*frames: pd.DataFrame | None) -> float:
    usable = [frame for frame in frames if frame is not None]
    if not usable:
        return 0.0
    known = pd.concat([frame.notna().stack() for frame in usable], axis=1).all(axis=1)
    return float(known.mean()) if len(known) else 0.0


def execution_mask(
    open_px: pd.DataFrame,
    pre_close: pd.DataFrame,
    *,
    is_st: pd.DataFrame | None = None,
    is_suspended: pd.DataFrame | None = None,
    limit_up: pd.DataFrame | None = None,
    limit_down: pd.DataFrame | None = None,
    limit_pct: float | None = 0.095,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build a conservative A-share open-auction execution mask.

    PIT security status and daily limit prices are the production evidence. The
    historical percentage-gap rule is retained only as a *research fallback* so
    old experiments remain reproducible; it is explicitly reported as synthetic
    and cannot satisfy a tradability/release contract.
    """
    open_px, pre_close = open_px.align(pre_close, join="inner", axis=None)
    valid = open_px.notna() & pre_close.notna() & (open_px > 0) & (pre_close > 0)
    index, columns = valid.index, valid.columns
    st = _align_optional(is_st, index, columns)
    suspended = _align_optional(is_suspended, index, columns)
    up = _align_optional(limit_up, index, columns)
    down = _align_optional(limit_down, index, columns)

    st_coverage = _coverage(st)
    suspension_coverage = _coverage(suspended)
    limit_coverage = _coverage(up, down)
    has_limit_prices = up is not None and down is not None
    if st is not None:
        valid &= ~st.fillna(False).astype(bool)
    if suspended is not None:
        valid &= ~suspended.fillna(False).astype(bool)

    if has_limit_prices:
        up_num = up.apply(pd.to_numeric, errors="coerce")
        down_num = down.apply(pd.to_numeric, errors="coerce")
        # 1bp accounts for vendor price rounding and protects both legs.
        at_limit = ((open_px >= up_num * (1 - 1e-4)) & (up_num > 0)) | (
            (open_px <= down_num * (1 + 1e-4)) & (down_num > 0)
        )
        limit_mode = "pit_prices"
    elif limit_pct is not None:
        gap = (open_px / pre_close - 1.0).abs()
        at_limit = gap >= float(limit_pct)
        limit_mode = "synthetic_gap_proxy"
    else:
        at_limit = pd.DataFrame(False, index=index, columns=columns)
        limit_mode = "none"
    mask = valid & ~at_limit
    return mask, {
        # Kept for compatibility with earlier reports; coverage is authoritative.
        "has_st_mask": is_st is not None,
        "has_suspension_mask": is_suspended is not None,
        "has_limit_prices": has_limit_prices,
        "st_coverage": st_coverage,
        "suspension_coverage": suspension_coverage,
        "limit_price_coverage": limit_coverage,
        "limit_mode": limit_mode,
        "limit_pct": float(limit_pct) if limit_pct is not None else None,
        "suspension_or_missing_pct": float((~valid).mean().mean()) if len(valid) else 1.0,
        "limit_open_pct": float(at_limit.mean().mean()) if len(at_limit) else 1.0,
    }


def simulate_non_overlapping_long_short(
    signal: pd.DataFrame,
    open_px: pd.DataFrame,
    close_px: pd.DataFrame,
    pre_close: pd.DataFrame,
    amount: pd.DataFrame | None = None,
    *,
    is_st: pd.DataFrame | None = None,
    is_suspended: pd.DataFrame | None = None,
    limit_up: pd.DataFrame | None = None,
    limit_down: pd.DataFrame | None = None,
    adv_20d: pd.DataFrame | None = None,
    free_float_shares: pd.DataFrame | None = None,
    trade_lag: int = 1,
    hold_days: int = 5,
    quantiles: int = 5,
    cost_bps: float = 10.0,
    adv_participation: float = 0.05,
    impact_bps: float = 0.0,
    limit_pct: float | None = 0.095,
    min_names_per_leg: int = 5,
) -> dict[str, Any]:
    """Simulate non-overlapping long/short orders under a T+1 open contract.

    Signals observed at T enter at T+lag open and close at the open after the
    holding period. Both orders must pass the PIT execution mask. ``close_px`` is
    still accepted for backwards-compatible callers and is used as a validity
    cross-check, but it is not an executable exit price.
    """
    if trade_lag < 1:
        raise ValueError("trade_lag must be >= 1 for production execution")
    if hold_days < 1 or quantiles < 2:
        raise ValueError("hold_days >= 1 and quantiles >= 2 are required")
    signal, open_px = signal.align(open_px, join="inner", axis=None)
    signal, close_px = signal.align(close_px, join="inner", axis=None)
    signal, pre_close = signal.align(pre_close, join="inner", axis=None)
    dates = list(signal.index.astype(str))
    mask, mask_meta = execution_mask(
        open_px,
        pre_close,
        is_st=is_st,
        is_suspended=is_suspended,
        limit_up=limit_up,
        limit_down=limit_down,
        limit_pct=limit_pct,
    )
    adv = _align_optional(adv_20d, signal.index, signal.columns)
    legacy_amount = _align_optional(amount, signal.index, signal.columns)
    float_shares = _align_optional(free_float_shares, signal.index, signal.columns)
    trades: list[dict[str, Any]] = []
    step = max(1, int(hold_days))
    start = 0
    while start + trade_lag + hold_days < len(dates):
        signal_i = start
        entry_i = start + trade_lag
        exit_i = entry_i + hold_days
        sig_date, entry_date, exit_date = dates[signal_i], dates[entry_i], dates[exit_i]
        sig = signal.iloc[signal_i]
        # Require both entry and exit opens to be executable. The close check
        # removes stale/missing price series but does not invent an exit fill.
        executable = (
            mask.iloc[entry_i]
            & mask.iloc[exit_i]
            & close_px.iloc[exit_i].notna()
            & (close_px.iloc[exit_i] > 0)
        )
        universe = sig.index[sig.notna() & executable]
        desired_n = max(min_names_per_leg, len(universe) // quantiles)
        if len(universe) < quantiles * min_names_per_leg:
            trades.append(
                {
                    "signal_date": sig_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "state": "skipped",
                    "reason": "insufficient_executable_names",
                    "n_executable": int(len(universe)),
                }
            )
            start += step
            continue
        ranked = sig.loc[universe].sort_values()
        short = ranked.index[:desired_n]
        long = ranked.index[-desired_n:]
        entry = open_px.iloc[entry_i]
        exit_px = open_px.iloc[exit_i]
        long_ret = (exit_px.loc[long] / entry.loc[long] - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
        short_ret = (entry.loc[short] / exit_px.loc[short] - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
        filled_long = int(len(long_ret))
        filled_short = int(len(short_ret))
        fill_rate = float((filled_long + filled_short) / max(1, 2 * desired_n))
        if min(filled_long, filled_short) < min_names_per_leg:
            trades.append(
                {
                    "signal_date": sig_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "state": "skipped",
                    "reason": "leg_fill_below_minimum",
                    "n_executable": int(len(universe)),
                    "fill_rate": fill_rate,
                }
            )
            start += step
            continue
        gross = float(long_ret.mean() + short_ret.mean())
        turnover = 2.0  # establish and close one unit of gross long-short exposure
        costs = turnover * (float(cost_bps) + float(impact_bps)) / 10_000.0
        capacity = None
        capacity_source = None
        cap_input = adv if adv is not None else legacy_amount
        if cap_input is not None:
            available = pd.concat([cap_input.iloc[entry_i].loc[long_ret.index], cap_input.iloc[entry_i].loc[short_ret.index]]).dropna()
            if not available.empty:
                capacity = float(pd.to_numeric(available, errors="coerce").dropna().min() * max(0.0, float(adv_participation)))
                capacity_source = "adv_20d" if adv is not None else "same_day_amount_proxy"
        trades.append(
            {
                "signal_date": sig_date,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "state": "filled",
                "n_long": filled_long,
                "n_short": filled_short,
                "fill_rate": fill_rate,
                "gross_long_short": gross,
                "cost": costs,
                "net_long_short": gross - costs,
                "turnover": turnover,
                "capacity_per_name": capacity,
                "capacity_source": capacity_source,
                "free_float_shares_coverage": float(float_shares.iloc[entry_i].notna().mean()) if float_shares is not None else 0.0,
            }
        )
        start += step
    filled = [t for t in trades if t["state"] == "filled"]
    net = [float(t["net_long_short"]) for t in filled]
    capacities = [float(t["capacity_per_name"]) for t in filled if t.get("capacity_per_name") is not None]
    limitations = []
    if mask_meta["st_coverage"] < 0.999:
        limitations.append("missing_point_in_time_st_mask")
    if mask_meta["suspension_coverage"] < 0.999:
        limitations.append("missing_point_in_time_suspension_mask")
    if mask_meta["limit_price_coverage"] < 0.999:
        limitations.append("missing_point_in_time_limit_prices")
    if adv is None or _coverage(adv) < 0.999:
        limitations.append("missing_adv_capacity_input")
    if adv is None and legacy_amount is not None:
        limitations.append("same_day_amount_used_as_adv_proxy")
    return {
        "contract_version": "non_overlapping_execution_v2",
        "trade_lag": int(trade_lag),
        "hold_days": int(hold_days),
        "quantiles": int(quantiles),
        "cost_bps": float(cost_bps),
        "impact_bps": float(impact_bps),
        "adv_participation": float(adv_participation),
        "n_rebalances": len(trades),
        "n_filled": len(filled),
        "fill_rate": float(np.mean([float(t.get("fill_rate", 0.0)) for t in trades])) if trades else 0.0,
        "gross_long_short_mean": float(np.mean([float(t["gross_long_short"]) for t in filled])) if filled else 0.0,
        "net_long_short_mean": float(np.mean(net)) if net else 0.0,
        "net_long_short_std": float(np.std(net, ddof=1)) if len(net) > 1 else 0.0,
        "capacity_per_name_median": float(np.median(capacities)) if capacities else None,
        "mask": mask_meta,
        "limitations": limitations,
        "trades": trades,
    }
