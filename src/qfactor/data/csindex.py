from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests

CSINDEX_CONS_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/cons/{code}cons.xls"
)
CSINDEX_WEIGHT_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/closeweight/{code}closeweight.xls"
)


def _pick_col(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        cl = str(c).lower()
        for n in needles:
            if n.lower() in cl or n in str(c):
                return c
    return None


def _code_to_ts(code: str, exchange: str | None = None) -> str:
    code = str(code).strip().zfill(6)
    if exchange:
        ex = str(exchange)
        if "深圳" in ex or "Shenzhen" in ex or ex.upper() in {"SZSE", "SZ"}:
            return f"{code}.SZ"
        if "上海" in ex or "Shanghai" in ex or ex.upper() in {"SSE", "SH"}:
            return f"{code}.SH"
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def fetch_csindex_members(
    index_code: str = "000903",
    timeout: float = 30.0,
) -> pd.DataFrame:
    """
    Official CSI / CSIndex constituent file (latest snapshot).
    No Tushare required. Historical point-in-time history is limited to
    whatever date the exchange file carries (usually latest close date).
    """
    code = index_code.replace(".SH", "").replace(".SZ", "").zfill(6)
    headers = {"User-Agent": "Mozilla/5.0 qfactor/0.1"}
    # Prefer weight file (has weight%); fall back to cons file.
    last_err: Exception | None = None
    df = None
    for url in (CSINDEX_WEIGHT_URL.format(code=code), CSINDEX_CONS_URL.format(code=code)):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
            r.raise_for_status()
            df = pd.read_excel(io.BytesIO(r.content))
            break
        except Exception as e:
            last_err = e
            df = None
    if df is None or df.empty:
        raise RuntimeError(f"CSIndex member download failed: {last_err}")

    cols = list(df.columns)
    date_col = _pick_col(cols, "Date", "日期")
    code_col = _pick_col(cols, "Constituent Code", "成分券代码", "成份券代码")
    name_col = _pick_col(cols, "Constituent Name", "成分券名称", "成份券名称")
    exch_col = _pick_col(cols, "Exchange", "交易所")
    weight_col = _pick_col(cols, "weight", "权重")
    if code_col is None:
        raise RuntimeError(f"Cannot find constituent code column in {cols}")

    trade_date = None
    if date_col is not None:
        raw = str(df[date_col].iloc[0])
        trade_date = raw.replace("-", "")[:8]
    if not trade_date or not trade_date.isdigit():
        trade_date = datetime.now(timezone.utc).strftime("%Y%m%d")

    out = pd.DataFrame(
        {
            "trade_date": trade_date,
            "ts_code": [
                _code_to_ts(c, None if exch_col is None else str(e))
                for c, e in zip(
                    df[code_col],
                    df[exch_col] if exch_col else [None] * len(df),
                )
            ],
            "name": df[name_col].astype(str) if name_col else "",
            "weight": pd.to_numeric(df[weight_col], errors="coerce")
            if weight_col
            else pd.NA,
            "source": "csindex",
        }
    )
    return out.drop_duplicates("ts_code").reset_index(drop=True)


def member_meta() -> dict[str, Any]:
    return {
        "provider": "csindex",
        "note": (
            "Official latest constituent/weight file. Full historical reconstitution "
            "requires vendor history (e.g. Tushare index_weight). We stamp the file date "
            "across the research window and document this limitation."
        ),
    }