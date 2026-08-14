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

    def fetch_index_members_history(self, start: str, end: str) -> pd.DataFrame:
        """Reconstitution snapshots. Default empty — providers may override."""
        return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])

    def fetch_security_status(self, start: str, end: str) -> pd.DataFrame:
        """Optional PIT execution flags: is_st, is_suspended, limit_up, limit_down."""
        return pd.DataFrame(
            columns=["trade_date", "ts_code", "is_st", "is_suspended", "limit_up", "limit_down"]
        )

    def fetch_corporate_actions(self, start: str, end: str) -> pd.DataFrame:
        """Optional PIT corporate-action/adjustment evidence per security and date."""
        return pd.DataFrame(columns=["trade_date", "ts_code", "corporate_action", "adj_factor_vendor"])

    def fetch_risk_exposures(self, start: str, end: str) -> pd.DataFrame:
        """Optional PIT style/risk exposures for later multi-factor risk controls."""
        return pd.DataFrame(columns=["trade_date", "ts_code"])

    def fetch_industry_history(self, start: str, end: str) -> pd.DataFrame:
        """Optional PIT classifications: trade_date, ts_code, industry."""
        return pd.DataFrame(columns=["trade_date", "ts_code", "industry"])


class SupportsPanel(Protocol):
    def panel(self, field: str) -> pd.DataFrame: ...