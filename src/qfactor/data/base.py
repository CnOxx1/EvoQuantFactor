from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

import pandas as pd


class DataAdapter(ABC):
    """Pluggable market-data source. Swap Tushare/AkShare/Wind without touching factors."""

    name: str

    @abstractmethod
    def fetch_trade_calendar(self, start: str, end: str) -> pd.DataFrame:
        """Return columns: cal_date (str YYYYMMDD), is_open (int)."""

    @abstractmethod
    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        """Return columns: trade_date, ts_code, weight(optional)."""

    @abstractmethod
    def fetch_daily_bars(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """Return OHLCV + pre_close for one symbol."""

    @abstractmethod
    def fetch_adj_factor(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """Return trade_date, ts_code, adj_factor."""

    def fetch_daily_basic(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """Optional turnover / market-cap fields. Default empty."""
        return pd.DataFrame(
            columns=["trade_date", "ts_code", "turnover_rate", "circ_mv"]
        )


class SupportsPanel(Protocol):
    def panel(self, field: str) -> pd.DataFrame: ...