from __future__ import annotations

import time
from typing import Any

import pandas as pd

from qfactor.data.base import DataAdapter
from qfactor.settings import get_settings


class TushareAdapter(DataAdapter):
    name = "tushare"

    def __init__(
        self,
        token: str | None = None,
        index_code: str = "000903.SH",
        sleep_seconds: float = 0.35,
    ):
        self.token = token or get_settings().tushare_token
        if not self.token:
            raise RuntimeError(
                "TUSHARE_TOKEN is empty. Set it in .env or use --source akshare."
            )
        import tushare as ts

        self.pro = ts.pro_api(self.token)
        self.index_code = index_code
        self.sleep_seconds = sleep_seconds

    def _sleep(self) -> None:
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

    def fetch_trade_calendar(self, start: str, end: str) -> pd.DataFrame:
        self._sleep()
        df = self.pro.trade_cal(
            exchange="SSE",
            start_date=start,
            end_date=end,
            fields="cal_date,is_open",
        )
        return df.sort_values("cal_date").reset_index(drop=True)

    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        self._sleep()
        df = self.pro.index_weight(index_code=self.index_code, trade_date=trade_date)
        if df is None or df.empty:
            return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])
        out = df.rename(columns={"con_code": "ts_code"})[
            ["trade_date", "ts_code", "weight"]
        ]
        return out

    def fetch_daily_bars(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        self._sleep()
        df = self.pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
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
                ]
            )
        cols = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "vol",
            "amount",
        ]
        return df[cols].sort_values("trade_date").reset_index(drop=True)

    def fetch_adj_factor(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        self._sleep()
        df = self.pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])
        return df[["ts_code", "trade_date", "adj_factor"]].sort_values("trade_date")

    def fetch_daily_basic(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        self._sleep()
        try:
            df = self.pro.daily_basic(
                ts_code=ts_code,
                start_date=start,
                end_date=end,
                fields="ts_code,trade_date,turnover_rate,circ_mv",
            )
        except Exception:
            return pd.DataFrame(
                columns=["trade_date", "ts_code", "turnover_rate", "circ_mv"]
            )
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["trade_date", "ts_code", "turnover_rate", "circ_mv"]
            )
        return df.sort_values("trade_date").reset_index(drop=True)

    def list_open_dates(self, start: str, end: str) -> list[str]:
        cal = self.fetch_trade_calendar(start, end)
        return cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist()


def adapter_kwargs_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    tcfg = cfg.get("tushare", {})
    return {
        "index_code": tcfg.get("index_code", "000903.SH"),
        "sleep_seconds": float(tcfg.get("sleep_seconds", 0.35)),
    }