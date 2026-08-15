from __future__ import annotations

from pathlib import Path

import pandas as pd

from qfactor.data.base import DataAdapter
from qfactor.data.vendor_normalize import normalize_panel


class ArchiveAdapter(DataAdapter):
    """Read immutable, externally-vetted PIT/valuation extracts from local files.

    It is intentionally not a market-bar adapter. Bars may continue to come from
    BaoStock or another source, while universe history and daily basic fields are
    supplied by independently validated archive extracts. Every archive path is
    included in the data-version metadata by the caller.
    """

    name = "archive"

    def __init__(
        self,
        universe_history: Path | None = None,
        daily_basic: Path | None = None,
        security_status: Path | None = None,
        corporate_actions: Path | None = None,
        risk_exposures: Path | None = None,
        industry_history: Path | None = None,
    ):
        self.universe_history = universe_history
        self.daily_basic = daily_basic
        self.security_status = security_status
        self.corporate_actions = corporate_actions
        self.risk_exposures = risk_exposures
        self.industry_history = industry_history

    @staticmethod
    def _read(path: Path | None) -> pd.DataFrame:
        if path is None or not path.exists():
            return pd.DataFrame()
        if path.suffix.lower() in {".parquet", ".pq"}:
            raw = pd.read_parquet(path)
        else:
            raw = pd.read_csv(path)
        return normalize_panel(raw)

    @staticmethod
    def _date(value: pd.Series) -> pd.Series:
        return value.astype(str).str.replace("-", "", regex=False).str[:8]

    def fetch_trade_calendar(self, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["cal_date", "is_open"])

    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        hist = self.fetch_index_members_history(trade_date, trade_date)
        return hist[hist["trade_date"] <= str(trade_date)[:8]] if not hist.empty else hist

    def fetch_index_members_history(self, start: str, end: str) -> pd.DataFrame:
        df = self._read(self.universe_history)
        if df.empty:
            return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])
        if "trade_date" not in df.columns and "cal_date" in df.columns:
            df = df.rename(columns={"cal_date": "trade_date"})
        if "ts_code" not in df.columns and "con_code" in df.columns:
            df = df.rename(columns={"con_code": "ts_code"})
        required = {"trade_date", "ts_code"}
        if not required.issubset(df.columns):
            raise ValueError(f"archive universe file missing columns: {sorted(required - set(df.columns))}")
        df = df.copy()
        df["trade_date"] = self._date(df["trade_date"])
        if "weight" not in df.columns:
            df["weight"] = pd.NA
        return (
            df.loc[df["trade_date"].between(str(start)[:8], str(end)[:8]), ["trade_date", "ts_code", "weight"]]
            .drop_duplicates(["trade_date", "ts_code"])
            .reset_index(drop=True)
        )

    def _date_frame(self, path: Path | None, required: set[str], label: str) -> pd.DataFrame:
        df = self._read(path)
        if df.empty:
            return pd.DataFrame(columns=sorted(required))
        if "cal_date" in df.columns and "trade_date" not in df.columns:
            df = df.rename(columns={"cal_date": "trade_date"})
        if "con_code" in df.columns and "ts_code" not in df.columns:
            df = df.rename(columns={"con_code": "ts_code"})
        if not required.issubset(df.columns):
            raise ValueError(f"archive {label} file missing columns: {sorted(required - set(df.columns))}")
        df = df.copy()
        df["trade_date"] = self._date(df["trade_date"])
        df["ts_code"] = df["ts_code"].astype(str)
        return df

    def _slice_dates(self, df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        if df.empty:
            return df
        return df.loc[df["trade_date"].between(str(start)[:8], str(end)[:8])].copy()

    def fetch_daily_bars(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        raise RuntimeError("ArchiveAdapter is for universe/daily-basic evidence, not bar downloads")

    def fetch_adj_factor(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["trade_date", "ts_code", "adj_factor"])

    def fetch_security_status(self, start: str, end: str) -> pd.DataFrame:
        required = {"trade_date", "ts_code"}
        df = self._date_frame(self.security_status, required, "security-status")
        columns = ["trade_date", "ts_code", "is_st", "is_suspended", "limit_up", "limit_down"]
        for col in columns:
            if col not in df.columns:
                df[col] = pd.NA
        return self._slice_dates(df, start, end)[columns].drop_duplicates(["trade_date", "ts_code"])

    def fetch_corporate_actions(self, start: str, end: str) -> pd.DataFrame:
        required = {"trade_date", "ts_code"}
        df = self._date_frame(self.corporate_actions, required, "corporate-actions")
        columns = ["trade_date", "ts_code", "corporate_action", "adj_factor_vendor"]
        for col in columns:
            if col not in df.columns:
                df[col] = pd.NA
        return self._slice_dates(df, start, end)[columns].drop_duplicates(["trade_date", "ts_code"])

    def fetch_risk_exposures(self, start: str, end: str) -> pd.DataFrame:
        df = self._date_frame(self.risk_exposures, {"trade_date", "ts_code"}, "risk-exposures")
        return self._slice_dates(df, start, end).drop_duplicates(["trade_date", "ts_code"])

    def fetch_industry_history(self, start: str, end: str) -> pd.DataFrame:
        df = self._date_frame(
            self.industry_history, {"trade_date", "ts_code", "industry"}, "industry-history"
        )
        return self._slice_dates(df, start, end)[["trade_date", "ts_code", "industry"]].drop_duplicates(
            ["trade_date", "ts_code"]
        )

    def fetch_daily_basic(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        df = self._read(self.daily_basic)
        columns = [
            "trade_date", "ts_code", "turnover_rate", "circ_mv", "free_float_shares", "adv_20d"
        ]
        if df.empty:
            return pd.DataFrame(columns=columns)
        if not {"trade_date", "ts_code"}.issubset(df.columns):
            raise ValueError("archive daily-basic file requires trade_date and ts_code")
        df = df.copy()
        df["trade_date"] = self._date(df["trade_date"])
        for col in ("turnover_rate", "circ_mv", "free_float_shares", "adv_20d"):
            if col not in df.columns:
                df[col] = pd.NA
        return (
            df.loc[
                (df["ts_code"].astype(str) == str(ts_code))
                & df["trade_date"].between(str(start)[:8], str(end)[:8]),
                columns,
            ]
            .drop_duplicates(["trade_date", "ts_code"])
            .reset_index(drop=True)
        )
