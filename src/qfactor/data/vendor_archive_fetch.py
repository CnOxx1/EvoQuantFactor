from __future__ import annotations

"""Pull candidate-grade PIT archives from a Tushare-compatible client.

Writes the three files the candidate contract needs: CSI100 members,
vendor daily_basic (circ_mv), and date-keyed industry. Does not invent
ST / limit / risk files. Token stays in the environment; never in git.
"""

import re
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

_AUTH_BUSY_RE = re.compile(r"等待\s*(\d+)\s*秒")

from qfactor.data.archive_ingest import ingest_archive_role
from qfactor.data.tushare_adapter import (
    CSI100_INDEX_CODES,
    fetch_index_weight_pages,
    import_ts_client,
    resolve_ts_token,
)
from qfactor.data.universe import normalize_members, universe_stats
from qfactor.settings import ProjectConfig, get_project_config


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _auth_lock_wait(exc: BaseException) -> float | None:
    msg = str(exc)
    if "授权码正在被其他设备使用" in msg:
        hit = _AUTH_BUSY_RE.search(msg)
        return float(int(hit.group(1)) + 5) if hit else 65.0
    if "超时" in msg or "timeout" in msg.lower():
        return 8.0
    return None


def _call_with_auth_retry(fn: Callable[[], Any], *, retries: int = 5) -> Any:
    last: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            wait = _auth_lock_wait(e)
            if wait is None:
                raise
            print(
                f"[vendor-archive] auth lock (attempt {attempt}/{retries}), sleep {wait:.0f}s",
                flush=True,
            )
            time.sleep(wait)
    assert last is not None
    raise last


def connect_pro(token: str | None = None) -> Any:
    tok = resolve_ts_token(token)
    if not tok:
        raise RuntimeError("Set TINYSHARE_TOKEN or TUSHARE_TOKEN to fetch vendor archives")
    ts = import_ts_client()
    if hasattr(ts, "set_token"):
        ts.set_token(tok)
    try:
        return ts.pro_api(tok)
    except TypeError:
        return ts.pro_api()


def _month_starts(start: str, end: str) -> list[tuple[str, str]]:
    cursor = pd.Timestamp(start[:4] + "-" + start[4:6] + "-01")
    last = pd.Timestamp(end[:4] + "-" + end[4:6] + "-01")
    out: list[tuple[str, str]] = []
    while cursor <= last:
        m_start = cursor.strftime("%Y%m%d")
        m_end = (cursor + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
        out.append((max(m_start, start), min(m_end, end)))
        cursor = cursor + pd.offsets.MonthBegin(1)
    return out


def fetch_csi100_members(
    pro: Any,
    start: str,
    end: str,
    *,
    sleep_seconds: float = 0.35,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for m_start, m_end in _month_starts(start, end):
        best = pd.DataFrame()
        for code in CSI100_INDEX_CODES:
            chunk = fetch_index_weight_pages(
                pro,
                index_code=code,
                start_date=m_start,
                end_date=m_end,
                sleep_fn=lambda: _sleep(sleep_seconds),
            )
            part = normalize_members(chunk)
            if len(part) > len(best):
                best = part
            if not best.empty and best.groupby("trade_date")["ts_code"].nunique().min() >= 80:
                break
        if not best.empty:
            frames.append(best)
        _sleep(sleep_seconds)
    if not frames:
        return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])
    return normalize_members(pd.concat(frames, ignore_index=True))


def fetch_daily_basic_union(
    pro: Any,
    codes: list[str],
    start: str,
    end: str,
    *,
    sleep_seconds: float = 0.35,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for i, code in enumerate(codes, start=1):
        _sleep(sleep_seconds)
        try:
            df = _call_with_auth_retry(
                lambda: pro.daily_basic(
                    ts_code=code,
                    start_date=start,
                    end_date=end,
                    fields="ts_code,trade_date,turnover_rate,circ_mv,free_share",
                )
            )
        except Exception as e:
            print(f"[vendor-archive] daily_basic {code} failed: {e}", flush=True)
            continue
        if df is None or df.empty:
            continue
        out = df.rename(columns={"free_share": "free_float_shares"})
        frames.append(out)
        if i % 20 == 0:
            print(f"[vendor-archive] daily_basic {i}/{len(codes)}", flush=True)
    if not frames:
        return pd.DataFrame(columns=["trade_date", "ts_code", "circ_mv", "turnover_rate", "free_float_shares"])
    return pd.concat(frames, ignore_index=True)


def _normalize_sw_roster(raw: pd.DataFrame, industry_fallback: str | None = None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ts_code", "industry", "in_date", "out_date"])
    df = raw.copy()
    if "ts_code" not in df.columns and "con_code" in df.columns:
        df = df.rename(columns={"con_code": "ts_code"})
    if "industry" not in df.columns:
        if "l1_name" in df.columns:
            df["industry"] = df["l1_name"]
        elif industry_fallback:
            df["industry"] = industry_fallback
        else:
            raise ValueError("SW roster has no industry or l1_name column")
    keep = df[["ts_code", "industry", "in_date", "out_date"]].copy()
    keep["ts_code"] = keep["ts_code"].astype(str)
    keep["in_date"] = keep["in_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    keep["out_date"] = keep["out_date"].fillna("99991231")
    keep["out_date"] = keep["out_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    keep.loc[keep["out_date"].isin({"", "nan", "None", "NaT", "<NA>"}), "out_date"] = "99991231"
    return keep.drop_duplicates()


def codes_missing_window_industry(
    roster: pd.DataFrame,
    codes: list[str],
    start: str,
    end: str,
) -> list[str]:
    """Union names with no SW interval overlapping [start, end]."""
    have: set[str] = set()
    if roster is not None and not roster.empty:
        for rec in roster.itertuples(index=False):
            in_d = str(rec.in_date)[:8]
            out_d = str(rec.out_date)[:8]
            if in_d <= end and out_d > start:
                have.add(str(rec.ts_code))
    return [c for c in codes if c not in have]


def fetch_sw_industry_roster(
    pro: Any,
    *,
    sleep_seconds: float = 0.25,
) -> pd.DataFrame:
    _sleep(sleep_seconds)
    cls = _call_with_auth_retry(lambda: pro.index_classify(level="L1", src="SW2021"))
    if cls is None or cls.empty:
        raise RuntimeError("index_classify(SW2021 L1) returned empty")
    frames: list[pd.DataFrame] = []
    for code, name in zip(cls["index_code"], cls["industry_name"]):
        for is_new in ("Y", "N"):
            _sleep(sleep_seconds)
            try:
                df = _call_with_auth_retry(
                    lambda c=code, flag=is_new: pro.index_member_all(l1_code=c, is_new=flag)
                )
            except Exception:
                df = None
            if df is None or getattr(df, "empty", True):
                continue
            part = df.copy()
            part["industry"] = name
            frames.append(part)
    if not frames:
        return pd.DataFrame(columns=["ts_code", "industry", "in_date", "out_date"])
    return _normalize_sw_roster(pd.concat(frames, ignore_index=True))


def fetch_sw_industry_for_codes(
    pro: Any,
    codes: list[str],
    *,
    sleep_seconds: float = 0.25,
) -> pd.DataFrame:
    """Per-name index_member_all. L1 sweeps on this proxy can drop current members."""
    frames: list[pd.DataFrame] = []
    for i, code in enumerate(codes, start=1):
        _sleep(sleep_seconds)
        try:
            df = _call_with_auth_retry(lambda c=code: pro.index_member_all(ts_code=c))
        except Exception as e:
            print(f"[vendor-archive] industry {code} failed: {e}", flush=True)
            continue
        if df is None or getattr(df, "empty", True):
            continue
        frames.append(df)
        if i % 20 == 0:
            print(f"[vendor-archive] industry fill {i}/{len(codes)}", flush=True)
    if not frames:
        return pd.DataFrame(columns=["ts_code", "industry", "in_date", "out_date"])
    return _normalize_sw_roster(pd.concat(frames, ignore_index=True))


def expand_industry_to_calendar(
    roster: pd.DataFrame,
    dates: list[str],
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """Expand in_date/out_date intervals onto trading days. out_date is exclusive."""
    if roster is None or roster.empty or not dates:
        return pd.DataFrame(columns=["trade_date", "ts_code", "industry"])
    df = roster.copy()
    if codes is not None:
        wanted = set(str(c) for c in codes)
        df = df[df["ts_code"].astype(str).isin(wanted)]
    if df.empty:
        return pd.DataFrame(columns=["trade_date", "ts_code", "industry"])
    df = df.sort_values(["ts_code", "in_date"])
    dates_i = pd.Index(sorted(str(d)[:8] for d in dates), name="trade_date")
    rows: list[pd.DataFrame] = []
    for code, grp in df.groupby("ts_code", sort=False):
        assigned = pd.Series(pd.NA, index=dates_i, dtype="object")
        for rec in grp.itertuples(index=False):
            start = str(rec.in_date)[:8]
            end = str(rec.out_date)[:8]
            hit = (dates_i >= start) & (dates_i < end)
            assigned.loc[hit] = rec.industry
        part = assigned.dropna().rename("industry").reset_index()
        if part.empty:
            continue
        part["ts_code"] = str(code)
        rows.append(part)
    if not rows:
        return pd.DataFrame(columns=["trade_date", "ts_code", "industry"])
    return pd.concat(rows, ignore_index=True)[["trade_date", "ts_code", "industry"]]


def _calendar_covers_window(dates: list[str], start: str, end: str) -> bool:
    """True when stored open days cover the start and end months.

    Window bounds may fall on weekends; compare YYYYMM so a Sunday
    `20191201` still matches a calendar that starts `20191202`.
    """
    if not dates:
        return False
    first, last = min(dates), max(dates)
    return first[:6] <= start[:6] and last[:6] >= end[:6]


def _calendar_dates(start: str, end: str, cfg: ProjectConfig) -> list[str]:
    cal_path = cfg.path("data_processed") / "calendar" / "trade_cal.parquet"
    if cal_path.exists():
        cal = pd.read_parquet(cal_path)
        col = "cal_date" if "cal_date" in cal.columns else "trade_date"
        open_col = "is_open" if "is_open" in cal.columns else None
        raw = cal[col].astype(str).str.replace("-", "", regex=False).str[:8]
        if open_col:
            raw = raw[cal[open_col].astype(int) == 1]
        stored = [d for d in raw.tolist() if d]
        # A 2024–2026 research calendar must not silently clip a 2019–2025 pull.
        if _calendar_covers_window(stored, start, end):
            return [d for d in stored if start <= d <= end]
    from qfactor.data.baostock_adapter import BaostockAdapter

    cal = BaostockAdapter().fetch_trade_calendar(start, end)
    return (
        cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist()
        if not cal.empty
        else []
    )


def _load_existing_members(cfg: ProjectConfig) -> pd.DataFrame:
    path = cfg.root / "data" / "raw" / "providers" / "csi100_members.parquet"
    if not path.exists():
        raise RuntimeError("csi100_members.parquet missing; fetch universe first")
    return pd.read_parquet(path)


def fetch_and_ingest_vendor_archives(
    *,
    start: str = "20150901",
    end: str = "20260630",
    token: str | None = None,
    sleep_seconds: float = 0.35,
    cfg: ProjectConfig | None = None,
    roles: list[str] | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_project_config()
    wanted = {str(r).strip().lower() for r in (roles or ["universe", "daily_basic", "industry"])}
    unknown = wanted - {"universe", "daily_basic", "industry"}
    if unknown:
        raise ValueError(f"unknown vendor archive roles: {sorted(unknown)}")
    pro = connect_pro(token)
    tmp = cfg.root / "data" / "raw" / "providers" / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"start": start, "end": end, "roles": sorted(wanted)}

    if "universe" in wanted:
        print(f"[vendor-archive] CSI100 members {start}–{end}", flush=True)
        members = fetch_csi100_members(pro, start, end, sleep_seconds=sleep_seconds)
        stats = universe_stats(members)
        if stats["n_snapshots"] < 2 or stats["n_codes_per_snapshot_mean"] < 80:
            raise RuntimeError(f"CSI100 member pull is not PIT-complete: {stats}")
        members_src = tmp / "csi100_members.csv"
        members.to_csv(members_src, index=False)
        members_report = ingest_archive_role("universe", members_src, cfg=cfg)
        report["members"] = {**stats, **{k: members_report.get(k) for k in ("path", "n_rows", "ok")}}
    else:
        members = _load_existing_members(cfg)

    codes = sorted(members["ts_code"].astype(str).unique().tolist())

    if "daily_basic" in wanted:
        print(f"[vendor-archive] daily_basic for {len(codes)} union names", flush=True)
        basic = fetch_daily_basic_union(pro, codes, start, end, sleep_seconds=sleep_seconds)
        if basic.empty or "circ_mv" not in basic.columns:
            raise RuntimeError("daily_basic pull returned no circ_mv")
        basic_src = tmp / "daily_basic.csv"
        basic.to_csv(basic_src, index=False)
        basic_report = ingest_archive_role("daily_basic", basic_src, cfg=cfg)
        report["daily_basic"] = {
            "n_rows": int(len(basic)),
            "n_codes": int(basic["ts_code"].nunique()),
            "circ_mv_coverage": float(basic["circ_mv"].notna().mean()),
            "path": basic_report.get("path"),
            "ok": basic_report.get("ok"),
        }

    if "industry" in wanted:
        roster_src = tmp / "sw_industry_roster.csv"
        roster = (
            _normalize_sw_roster(pd.read_csv(roster_src, dtype=str))
            if roster_src.exists()
            else pd.DataFrame(columns=["ts_code", "industry", "in_date", "out_date"])
        )
        missing = codes_missing_window_industry(roster, codes, start, end)
        if missing:
            print(
                f"[vendor-archive] SW2021 industry by ts_code for {len(missing)} missing names",
                flush=True,
            )
            extra = fetch_sw_industry_for_codes(
                pro, missing, sleep_seconds=min(sleep_seconds, 0.3)
            )
            roster = pd.concat([roster, extra], ignore_index=True).drop_duplicates()
        still_missing = codes_missing_window_industry(roster, codes, start, end)
        if still_missing:
            print(
                f"[vendor-archive] L1 sweep to fill {len(still_missing)} names still missing",
                flush=True,
            )
            try:
                extra = fetch_sw_industry_roster(pro, sleep_seconds=min(sleep_seconds, 0.3))
                roster = pd.concat([roster, extra], ignore_index=True).drop_duplicates()
            except Exception as e:
                print(f"[vendor-archive] L1 sweep failed: {e}", flush=True)
        roster.to_csv(roster_src, index=False)
        dates = _calendar_dates(start, end, cfg)
        print(
            f"[vendor-archive] expand industry onto {len(dates)} calendar days "
            f"{dates[0] if dates else '-'}–{dates[-1] if dates else '-'}",
            flush=True,
        )
        industry = expand_industry_to_calendar(roster, dates, codes)
        if industry.empty:
            raise RuntimeError("industry expansion produced no rows")
        industry_src = tmp / "industry_history.csv"
        industry.to_csv(industry_src, index=False)
        industry_report = ingest_archive_role("industry", industry_src, cfg=cfg)
        report["industry"] = {
            "n_rows": int(len(industry)),
            "n_codes": int(industry["ts_code"].nunique()),
            "n_dates": int(industry["trade_date"].nunique()),
            "date_min": str(industry["trade_date"].min()),
            "date_max": str(industry["trade_date"].max()),
            "path": industry_report.get("path"),
            "ok": industry_report.get("ok"),
        }

    print(report, flush=True)
    return report
