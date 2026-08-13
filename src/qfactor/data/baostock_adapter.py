from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import numpy as np
import pandas as pd

from qfactor.data.base import DataAdapter


@contextmanager
def bs_session() -> Iterator:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        yield bs
    finally:
        bs.logout()


def _to_bs_code(ts_code: str) -> str:
    code, mkt = ts_code.split(".")
    return f"{'sh' if mkt == 'SH' else 'sz'}.{code}"


def _ymd(s: str) -> str:
    s = s.replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


class BaostockAdapter(DataAdapter):
    """Reliable free bars + industry/turnover. Members come from CSIndex helper."""

    name = "baostock"

    def __init__(self, index_code: str = "sh.000903"):
        self.index_code = index_code
        self._bs = None

    def bind_session(self, bs) -> None:
        self._bs = bs

    def _api(self):
        if self._bs is not None:
            return self._bs
        raise RuntimeError("Baostock session not bound; use bs_session() context")

    def fetch_trade_calendar(self, start: str, end: str) -> pd.DataFrame:
        own = self._bs is None
        cm = bs_session() if own else nullcontext(self._bs)
        with cm as bs:
            if own:
                self._bs = bs
            rs = bs.query_trade_dates(start_date=_ymd(start), end_date=_ymd(end))
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            fields = rs.fields
        if own:
            self._bs = None
        if not rows:
            return pd.DataFrame(columns=["cal_date", "is_open"])
        df = pd.DataFrame(rows, columns=fields)
        df = df.rename(columns={"calendar_date": "cal_date", "is_trading_day": "is_open"})
        df["cal_date"] = df["cal_date"].str.replace("-", "", regex=False)
        df["is_open"] = df["is_open"].astype(int)
        return df[["cal_date", "is_open"]].sort_values("cal_date").reset_index(drop=True)

    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        from qfactor.data.csindex import fetch_csindex_members

        df = fetch_csindex_members("000903")
        # Stamp requested trade_date for universe asof usage when only latest file exists.
        df = df.copy()
        df["file_date"] = df["trade_date"]
        df["trade_date"] = trade_date
        return df

    def fetch_daily_bars(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        fields = "date,code,open,high,low,close,preclose,volume,amount,turn,peTTM,pbMRQ"
        bs = self._api()
        rs = bs.query_history_k_data_plus(
            _to_bs_code(ts_code),
            fields,
            start_date=_ymd(start),
            end_date=_ymd(end),
            frequency="d",
            adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame(
                columns=[
                    "ts_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "pre_close",
                    "vol",
                    "amount",
                    "turnover_rate",
                    "circ_mv",
                    "pe_ttm",
                    "pb",
                ]
            )
        df = pd.DataFrame(rows, columns=rs.fields)
        amount = pd.to_numeric(df["amount"], errors="coerce")
        turn = pd.to_numeric(df["turn"], errors="coerce")
        # circ_mv (万元近似): amount(元) / (turn%/100) / 10000
        circ_mv = amount / (turn.replace(0, np.nan) / 100.0) / 10000.0
        out = pd.DataFrame(
            {
                "ts_code": ts_code,
                "trade_date": df["date"].str.replace("-", "", regex=False),
                "open": pd.to_numeric(df["open"], errors="coerce"),
                "high": pd.to_numeric(df["high"], errors="coerce"),
                "low": pd.to_numeric(df["low"], errors="coerce"),
                "close": pd.to_numeric(df["close"], errors="coerce"),
                "pre_close": pd.to_numeric(df["preclose"], errors="coerce"),
                "vol": pd.to_numeric(df["volume"], errors="coerce") / 100.0,
                "amount": amount / 1000.0,
                "turnover_rate": turn,
                "circ_mv": circ_mv,
                "pe_ttm": pd.to_numeric(df["peTTM"], errors="coerce"),
                "pb": pd.to_numeric(df["pbMRQ"], errors="coerce"),
            }
        )
        return out.dropna(subset=["close"]).reset_index(drop=True)

    def fetch_adj_factor(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

    def fetch_daily_basic(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(
            columns=["trade_date", "ts_code", "turnover_rate", "circ_mv"]
        )

    def fetch_industry_map(self, ts_codes: list[str]) -> pd.DataFrame:
        bs = self._api()
        rows = []
        for ts in ts_codes:
            rs = bs.query_stock_industry(code=_to_bs_code(ts))
            last = None
            while rs.error_code == "0" and rs.next():
                last = rs.get_row_data()
            if last:
                rows.append(
                    {
                        "ts_code": ts,
                        "industry": last[3] if len(last) > 3 else "",
                        "industry_source": last[4] if len(last) > 4 else "baostock",
                    }
                )
        return pd.DataFrame(rows)


class nullcontext:
    def __init__(self, enter_result):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *args):
        return False
