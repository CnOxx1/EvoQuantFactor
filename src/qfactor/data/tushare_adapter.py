from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd

from qfactor.data.base import DataAdapter
from qfactor.settings import get_settings

# CSI100 also lists as 399903.SZ. Some Tushare-compatible proxies return a
# one-row first page for 000903.SH on 2024+ months; 399903.SZ is often complete.
CSI100_INDEX_CODES = ("000903.SH", "399903.SZ")


def _permission_denied(exc: BaseException) -> bool:
    msg = str(exc)
    return "没有接口" in msg or "访问权限" in msg


def resolve_ts_token(token: str | None = None) -> str:
    return (
        token
        or os.environ.get("TINYSHARE_TOKEN", "").strip()
        or get_settings().tushare_token
    )


def import_ts_client():
    """Official tushare, or tinyshare when requested / the only installed client."""
    backend = os.environ.get("QFACTOR_TS_CLIENT", "").strip().lower()
    use_tiny = backend == "tinyshare" or bool(os.environ.get("TINYSHARE_TOKEN", "").strip())
    if use_tiny:
        import tinyshare as ts

        return ts
    try:
        import tushare as ts
    except ImportError:
        import tinyshare as ts  # type: ignore[no-redef]
    return ts


def fetch_index_weight_pages(
    pro: Any,
    *,
    index_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_date: str | None = None,
    page_size: int = 100,
    sleep_fn: Any | None = None,
    max_rows: int = 20000,
) -> pd.DataFrame:
    """Page `index_weight`. Some proxies return only the first row until offset>0."""
    frames: list[pd.DataFrame] = []
    seen: set[tuple[str, str]] = set()
    offset = 0
    while offset < max_rows:
        kwargs: dict[str, Any] = {
            "index_code": index_code,
            "offset": offset,
            "limit": page_size,
        }
        if trade_date:
            kwargs["trade_date"] = trade_date
        else:
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
        df = pro.index_weight(**kwargs)
        if df is None or getattr(df, "empty", True):
            break
        code_col = "con_code" if "con_code" in df.columns else "ts_code"
        date_col = "trade_date" if "trade_date" in df.columns else None
        if code_col not in df.columns:
            break
        new_rows = []
        for _, row in df.iterrows():
            key = (
                str(row[date_col]) if date_col else "",
                str(row[code_col]),
            )
            if key in seen:
                continue
            seen.add(key)
            new_rows.append(row)
        if not new_rows:
            break
        frames.append(pd.DataFrame(new_rows))
        offset += int(len(df))
        if sleep_fn:
            sleep_fn()
    if not frames:
        return pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"])
    return pd.concat(frames, ignore_index=True)


class TushareAdapter(DataAdapter):
    name = "tushare"

    def __init__(
        self,
        token: str | None = None,
        index_code: str = "000903.SH",
        sleep_seconds: float = 0.35,
    ):
        self.token = resolve_ts_token(token)
        if not self.token:
            raise RuntimeError(
                "TUSHARE_TOKEN is empty. Set it in .env or use --source akshare."
            )
        ts = import_ts_client()
        if hasattr(ts, "set_token"):
            ts.set_token(self.token)
        try:
            self.pro = ts.pro_api(self.token)
        except TypeError:
            self.pro = ts.pro_api()
        self.index_code = index_code
        self.sleep_seconds = sleep_seconds

    def _sleep(self) -> None:
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

    def fetch_trade_calendar(self, start: str, end: str) -> pd.DataFrame:
        """Baostock trading days. Production never calls Tushare trade_cal."""
        from qfactor.data.baostock_adapter import BaostockAdapter

        return BaostockAdapter().fetch_trade_calendar(start, end)

    def _index_weight(self, **kwargs: Any) -> pd.DataFrame:
        return fetch_index_weight_pages(self.pro, sleep_fn=self._sleep, **kwargs)

    def fetch_index_members(self, trade_date: str) -> pd.DataFrame:
        from qfactor.data.universe import normalize_members

        df = self._index_weight(index_code=self.index_code, trade_date=trade_date)
        out = normalize_members(df)
        if len(out) >= 80:
            return out
        if self.index_code in CSI100_INDEX_CODES:
            for alt in CSI100_INDEX_CODES:
                if alt == self.index_code:
                    continue
                extra = normalize_members(
                    self._index_weight(index_code=alt, trade_date=trade_date)
                )
                if len(extra) > len(out):
                    out = extra
        return out if not out.empty else pd.DataFrame(columns=["trade_date", "ts_code", "weight"])

    def fetch_index_members_history(self, start: str, end: str) -> pd.DataFrame:
        """CSI reconstitutions: prefer in/out roster, else monthly index_weight."""
        from qfactor.data.universe import members_from_in_out

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

        try:
            df = self._index_weight(
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
        per_date = (
            out.groupby("trade_date")["ts_code"].nunique() if not out.empty else pd.Series(dtype=float)
        )
        if (
            not out.empty
            and out["trade_date"].nunique() >= max(2, expected_months // 3)
            and float(per_date.median()) >= 80
        ):
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
            try:
                chunk = self._index_weight(
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