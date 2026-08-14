from __future__ import annotations

import time
from typing import Any

import pandas as pd

from qfactor.data.base import DataAdapter
from qfactor.settings import get_settings


def _permission_denied(exc: BaseException) -> bool:
    msg = str(exc)
    return "没有接口" in msg or "访问权限" in msg


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
        """Baostock trading days. Production never calls Tushare trade_cal."""
        from qfactor.data.baostock_adapter import BaostockAdapter

        return BaostockAdapter().fetch_trade_calendar(start, end)

    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        self._sleep()
        df = self.pro.index_weight(index_code=self.index_code, trade_date=trade_date)
        if df is None or df.empty:
            return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])
        out = df.rename(columns={"con_code": "ts_code"})[
            ["trade_date", "ts_code", "weight"]
        ]
        return out

    def fetch_index_members_history(self, start: str, end: str) -> pd.DataFrame:
        """CSI reconstitutions: prefer in/out roster, else monthly index_weight."""
        from qfactor.data.universe import members_from_in_out, normalize_members

        roster, roster_perm = self._fetch_index_member_roster()
        if roster is not None and not roster.empty:
            expanded = members_from_in_out(roster, start, end)
            if not expanded.empty:
                return expanded
        try:
            return self._fetch_index_weight_range(start, end)
        except RuntimeError:
            raise
        except Exception as e:
            if _permission_denied(e) or roster_perm:
                raise RuntimeError(
                    "TUSHARE_TOKEN is set but this account cannot read CSI100 "
                    "reconstitutions (index_member / index_weight). "
                    "Need those APIs for PIT; do not fall back to a latest snapshot."
                ) from e
            raise

    def _fetch_index_member_roster(self) -> tuple[pd.DataFrame, bool]:
        perm = False
        for api_name in ("index_member", "index_member_all"):
            fn = getattr(self.pro, api_name, None)
            if fn is None:
                continue
            self._sleep()
            try:
                df = fn(index_code=self.index_code)
            except Exception as e:
                perm = perm or _permission_denied(e)
                continue
            if df is None or df.empty:
                continue
            if "in_date" in df.columns and (
                "ts_code" in df.columns or "con_code" in df.columns
            ):
                return df, False
        return pd.DataFrame(), perm

    def _fetch_index_weight_range(self, start: str, end: str) -> pd.DataFrame:
        from qfactor.data.universe import normalize_members

        self._sleep()
        try:
            df = self.pro.index_weight(
                index_code=self.index_code, start_date=start, end_date=end
            )
        except Exception as e:
            if _permission_denied(e):
                raise RuntimeError(
                    "TUSHARE_TOKEN is set but this account has no index_weight "
                    "permission. PIT CSI100 reconstitutions cannot be fetched."
                ) from e
            df = None
        out = normalize_members(df)
        expected_months = max(
            1,
            (
                pd.Timestamp(end[:4] + "-" + end[4:6] + "-01")
                - pd.Timestamp(start[:4] + "-" + start[4:6] + "-01")
            ).days
            // 28,
        )
        if not out.empty and out["trade_date"].nunique() >= max(2, expected_months // 3):
            return out
        frames = [] if out.empty else [out]
        cursor = pd.Timestamp(start[:4] + "-" + start[4:6] + "-01")
        last = pd.Timestamp(end[:4] + "-" + end[4:6] + "-01")
        seen = set(out["trade_date"].astype(str)) if not out.empty else set()
        while cursor <= last:
            m_start = cursor.strftime("%Y%m%d")
            m_end = (cursor + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
            if m_end < start:
                cursor = cursor + pd.offsets.MonthBegin(1)
                continue
            self._sleep()
            try:
                chunk = self.pro.index_weight(
                    index_code=self.index_code, start_date=m_start, end_date=min(m_end, end)
                )
            except Exception as e:
                if _permission_denied(e):
                    raise RuntimeError(
                        "TUSHARE_TOKEN is set but this account has no index_weight "
                        "permission. PIT CSI100 reconstitutions cannot be fetched."
                    ) from e
                chunk = None
            part = normalize_members(chunk)
            if not part.empty:
                part = part[~part["trade_date"].astype(str).isin(seen)]
                if not part.empty:
                    frames.append(part)
                    seen.update(part["trade_date"].astype(str))
            cursor = cursor + pd.offsets.MonthBegin(1)
        if not frames:
            return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])
        return normalize_members(pd.concat(frames, ignore_index=True))

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