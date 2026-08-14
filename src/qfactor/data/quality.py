from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class QualityReport:
    n_rows: int
    n_codes: int
    start: str | None
    end: str | None
    missing_close_pct: float
    duplicate_keys: int
    status_coverage_pct: float
    st_rate_pct: float
    suspension_rate_pct: float
    limit_price_coverage_pct: float
    limit_hit_rate_pct: float
    adv_20d_coverage_pct: float
    nonpositive_adv_20d_pct: float
    free_float_shares_coverage_pct: float
    corporate_action_coverage_pct: float
    vendor_adj_factor_coverage_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


def _coverage(df: pd.DataFrame, columns: list[str]) -> float:
    if not columns or not set(columns).issubset(df.columns) or df.empty:
        return 0.0
    return float(df[columns].notna().all(axis=1).mean())


def _bool_rate(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns or df.empty:
        return 0.0
    known = df[column].dropna()
    if known.empty:
        return 0.0
    values = known.map(
        {
            True: True,
            False: False,
            1: True,
            0: False,
            "1": True,
            "0": False,
            "true": True,
            "false": False,
            "True": True,
            "False": False,
        }
    ).dropna()
    return float(values.mean()) if not values.empty else 0.0


def _limit_hit_rate(df: pd.DataFrame) -> float:
    required = {"high", "low", "limit_up", "limit_down"}
    if df.empty or not required.issubset(df.columns):
        return 0.0
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    up = pd.to_numeric(df["limit_up"], errors="coerce")
    down = pd.to_numeric(df["limit_down"], errors="coerce")
    known = (up > 0) & (down > 0) & high.notna() & low.notna()
    if not known.any():
        return 0.0
    # Prices are rounded by vendors. A 1bp tolerance prevents precision artifacts.
    hit = (high >= up * (1 - 1e-4)) | (low <= down * (1 + 1e-4))
    return float(hit.loc[known].mean())


def check_daily_panel(df: pd.DataFrame) -> QualityReport:
    if df.empty:
        return QualityReport(
            0, 0, None, None, 1.0, 0,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        )
    key_dup = int(df.duplicated(["trade_date", "ts_code"]).sum())
    missing = float(df["close"].isna().mean()) if "close" in df.columns else 1.0
    adv = (
        pd.to_numeric(df["adv_20d"], errors="coerce")
        if "adv_20d" in df.columns
        else pd.Series(np.nan, index=df.index)
    )
    known_adv = adv.notna()
    return QualityReport(
        n_rows=len(df),
        n_codes=int(df["ts_code"].nunique()),
        start=str(df["trade_date"].min()),
        end=str(df["trade_date"].max()),
        missing_close_pct=missing,
        duplicate_keys=key_dup,
        status_coverage_pct=_coverage(df, ["is_st", "is_suspended"]),
        st_rate_pct=_bool_rate(df, "is_st"),
        suspension_rate_pct=_bool_rate(df, "is_suspended"),
        limit_price_coverage_pct=_coverage(df, ["limit_up", "limit_down"]),
        limit_hit_rate_pct=_limit_hit_rate(df),
        adv_20d_coverage_pct=float(known_adv.mean()),
        nonpositive_adv_20d_pct=float((adv.loc[known_adv] <= 0).mean()) if known_adv.any() else 0.0,
        free_float_shares_coverage_pct=_coverage(df, ["free_float_shares"]),
        # An explicit "none" action is valid coverage; null remains unknown.
        corporate_action_coverage_pct=_coverage(df, ["corporate_action"]),
        vendor_adj_factor_coverage_pct=_coverage(df, ["adj_factor_vendor"]),
    )
