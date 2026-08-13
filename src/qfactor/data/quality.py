from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd


@dataclass
class QualityReport:
    n_rows: int
    n_codes: int
    start: str | None
    end: str | None
    missing_close_pct: float
    duplicate_keys: int

    def to_dict(self) -> dict:
        return asdict(self)


def check_daily_panel(df: pd.DataFrame) -> QualityReport:
    if df.empty:
        return QualityReport(0, 0, None, None, 1.0, 0)
    key_dup = int(df.duplicated(["trade_date", "ts_code"]).sum())
    missing = float(df["close"].isna().mean()) if "close" in df.columns else 1.0
    return QualityReport(
        n_rows=len(df),
        n_codes=int(df["ts_code"].nunique()),
        start=str(df["trade_date"].min()),
        end=str(df["trade_date"].max()),
        missing_close_pct=missing,
        duplicate_keys=key_dup,
    )