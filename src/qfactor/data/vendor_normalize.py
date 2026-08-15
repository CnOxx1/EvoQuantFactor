from __future__ import annotations

import pandas as pd

# Vendor exports (Wind / Choice / RQData / CSIndex / AkShare) keep their own
# column names and exchange suffixes. The production contract only accepts
# trade_date + ts_code. This module renames known aliases; it does not invent
# ST, limit, industry, or circ_mv from bars.

_EXCHANGE = {
    "XSHG": "SH",
    "XSHE": "SZ",
    "SSE": "SH",
    "SZSE": "SZ",
    "SH": "SH",
    "SZ": "SZ",
}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": (
        "trade_date",
        "cal_date",
        "TRADE_DT",
        "TRADE_DATE",
        "tradeDate",
        "datetime",
        "date",
        "交易日期",
        "日期",
    ),
    "ts_code": (
        "ts_code",
        "con_code",
        "S_INFO_WINDCODE",
        "wind_code",
        "order_book_id",
        "symbol",
        "ticker",
        "证券代码",
        "成分券代码",
        "成份券代码",
        "品种代码",
        "code",
    ),
    "weight": ("weight", "i_weight", "weight_pct", "权重"),
    "circ_mv": (
        "circ_mv",
        "S_VAL_MV",
        "negotiable_mv",
        "negotiablemv",
        "float_a_mv",
        "float_mv",
        "流通市值",
    ),
    "turnover_rate": ("turnover_rate", "TURNOVER_RATE", "turnover", "换手率"),
    "free_float_shares": (
        "free_float_shares",
        "float_share",
        "FREE_SHARES",
        "流通股本",
    ),
    "adv_20d": ("adv_20d", "adv", "ADV"),
    "is_st": ("is_st", "st", "ST", "name_st"),
    "is_suspended": ("is_suspended", "suspend", "is_halt", "halt", "停牌"),
    "limit_up": ("limit_up", "up_limit", "S_DQ_LIMIT", "涨停价"),
    "limit_down": ("limit_down", "down_limit", "S_DQ_STOPPING", "跌停价"),
    "corporate_action": ("corporate_action", "event", "action_type", "事件类型"),
    "adj_factor_vendor": ("adj_factor_vendor", "adj_factor", "复权因子"),
    "industry": ("industry", "industry_name", "sw_l1", "sw1", "行业"),
}


def normalize_trade_date(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    raw = str(value).strip().replace("-", "").replace("/", "").replace(".", "")
    raw = raw.replace(" ", "")[:8]
    if len(raw) == 8 and raw.isdigit():
        return raw
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.strftime("%Y%m%d")


def normalize_ts_code(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    original = str(value).strip()
    raw = original.upper().replace(" ", "").replace("．", ".")
    if not raw or raw in {"NAN", "NONE", "<NA>", "NAT"}:
        return None
    if len(raw) >= 8 and raw[:2] in {"SH", "SZ"} and any(ch.isdigit() for ch in raw[2:]):
        digits = "".join(ch for ch in raw[2:] if ch.isdigit())[:6]
        if len(digits) == 6:
            return f"{digits}.{raw[:2]}"
    if "." in raw:
        code, exch = raw.split(".", 1)
        digits = "".join(ch for ch in code if ch.isdigit())[:6]
        exch = _EXCHANGE.get(exch, exch)
        if exch in {"SH", "SZ"} and len(digits) == 6:
            return f"{digits}.{exch}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        code = digits[-6:]
        return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
    return original


def rename_vendor_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename known vendor aliases onto contract names. Unknown columns stay."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    lower_map = {str(c).strip().lower(): c for c in out.columns}
    mapping: dict[str, str] = {}
    used_src: set[str] = set()
    for dest, aliases in COLUMN_ALIASES.items():
        if dest in out.columns:
            continue
        for alias in aliases:
            if alias == dest:
                continue
            src = alias if alias in out.columns else lower_map.get(alias.lower())
            if src is None or src in used_src or src == dest:
                continue
            mapping[src] = dest
            used_src.add(src)
            break
    if mapping:
        out = out.rename(columns=mapping)
    return out


def normalize_panel(df: pd.DataFrame | None) -> pd.DataFrame:
    """Apply alias rename plus date/code normalization. Drops unmappable keys."""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = rename_vendor_columns(df)
    if "trade_date" in out.columns:
        out["trade_date"] = out["trade_date"].map(normalize_trade_date)
    if "ts_code" in out.columns:
        out["ts_code"] = out["ts_code"].map(normalize_ts_code)
    return out
