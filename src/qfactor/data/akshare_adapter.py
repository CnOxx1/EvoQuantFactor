from __future__ import annotations

import time

import pandas as pd

from qfactor.data.base import DataAdapter


def _retry(fn, retries: int = 4, sleep: float = 1.2):
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(sleep * (i + 1))
    raise last  # type: ignore[misc]


class AkshareAdapter(DataAdapter):
    """Fallback adapter. Constituent history may be less precise than Tushare."""

    name = "akshare"

    def __init__(self, index_symbol: str = "sh000903", sleep_seconds: float = 0.4):
        self.index_symbol = index_symbol
        self.sleep_seconds = sleep_seconds

    def _pause(self) -> None:
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

    def fetch_trade_calendar(self, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        self._pause()

        def _call():
            df = ak.stock_zh_index_daily(symbol=self.index_symbol)
            df = df.rename(columns={"date": "cal_date"})
            df["cal_date"] = pd.to_datetime(df["cal_date"]).dt.strftime("%Y%m%d")
            df = df[(df["cal_date"] >= start) & (df["cal_date"] <= end)]
            out = df[["cal_date"]].drop_duplicates().sort_values("cal_date")
            out["is_open"] = 1
            return out.reset_index(drop=True)

        return _retry(_call)

    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        import akshare as ak

        self._pause()

        def _call():
            try:
                raw = ak.index_stock_cons_csindex(symbol="000903")
            except Exception:
                raw = ak.index_stock_cons(symbol="000903")
            code_col = "成分券代码" if "成分券代码" in raw.columns else "品种代码"
            if code_col not in raw.columns:
                # last fallback column guess
                for c in raw.columns:
                    if "代码" in str(c):
                        codes = raw[c].astype(str).str.zfill(6)
                        break
                else:
                    raise RuntimeError(f"Cannot find code column in {list(raw.columns)}")
            else:
                codes = raw[code_col].astype(str).str.zfill(6)
            ts_codes = codes.map(lambda x: f"{x}.SH" if x.startswith("6") else f"{x}.SZ")
            return pd.DataFrame(
                {
                    "trade_date": trade_date,
                    "ts_code": ts_codes,
                    "weight": pd.NA,
                }
            )

        return _retry(_call)

    def _to_ak_symbol(self, ts_code: str) -> str:
        code, mkt = ts_code.split(".")
        return f"{'sh' if mkt == 'SH' else 'sz'}{code}"

    def fetch_daily_bars(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak

        self._pause()
        symbol = self._to_ak_symbol(ts_code)

        def _call():
            return ak.stock_zh_a_hist(
                symbol=symbol[2:],
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )

        try:
            df = _retry(_call)
        except Exception:
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
        rename = {
            "日期": "trade_date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "vol",
            "成交额": "amount",
        }
        df = df.rename(columns=rename)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
        df["ts_code"] = ts_code
        df["pre_close"] = df["close"].shift(1)
        df["amount"] = df["amount"] / 1000.0
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
        return df[cols].dropna(subset=["pre_close"]).reset_index(drop=True)

    def fetch_adj_factor(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        # Avoid a second network fetch; qfq already applied in fetch_daily_bars.
        return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

    def fetch_daily_basic(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(
            columns=["trade_date", "ts_code", "turnover_rate", "circ_mv"]
        )