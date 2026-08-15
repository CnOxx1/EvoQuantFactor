from __future__ import annotations

from typing import Any

import pandas as pd

from qfactor.settings import ProjectConfig, get_project_config

MEMBERS_COLS = ["trade_date", "ts_code", "weight"]


def universe_policy(cfg: ProjectConfig | None = None) -> dict[str, Any]:
    cfg = cfg or get_project_config()
    raw = cfg.project.get("universe_policy") or {}
    dsc = (cfg.data_sources.get("universe") or {}) if isinstance(cfg.data_sources, dict) else {}
    mode = str(raw.get("mode") or dsc.get("mode") or "pit").strip().lower()
    if mode not in {"pit", "freeze_start", "snapshot"}:
        mode = "pit"
    return {
        "mode": mode,
        "lookback_days": int(raw.get("lookback_days") or dsc.get("lookback_days") or 120),
        "index_code": str(
            raw.get("index_code")
            or (cfg.data_sources.get("tushare") or {}).get("index_code")
            or "000903.SH"
        ),
    }


def shift_yyyymmdd(date: str, days: int) -> str:
    ts = pd.Timestamp(str(date))
    return (ts + pd.Timedelta(days=int(days))).strftime("%Y%m%d")


def normalize_members(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=MEMBERS_COLS)
    out = df.copy()
    rename = {}
    if "con_code" in out.columns and "ts_code" not in out.columns:
        rename["con_code"] = "ts_code"
    if "cal_date" in out.columns and "trade_date" not in out.columns:
        rename["cal_date"] = "trade_date"
    if rename:
        out = out.rename(columns=rename)
    if "ts_code" not in out.columns or "trade_date" not in out.columns:
        return pd.DataFrame(columns=MEMBERS_COLS)
    out["trade_date"] = out["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    out["ts_code"] = out["ts_code"].astype(str)
    if "weight" not in out.columns:
        out["weight"] = pd.NA
    keep = [c for c in MEMBERS_COLS if c in out.columns]
    out = out[keep].dropna(subset=["trade_date", "ts_code"])
    return out.drop_duplicates(["trade_date", "ts_code"]).reset_index(drop=True)


def members_from_in_out(
    roster: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Expand Tushare in_date/out_date roster into asof snapshots."""
    if roster is None or roster.empty:
        return pd.DataFrame(columns=MEMBERS_COLS)
    df = roster.copy()
    if "con_code" in df.columns and "ts_code" not in df.columns:
        df = df.rename(columns={"con_code": "ts_code"})
    if "ts_code" not in df.columns:
        return pd.DataFrame(columns=MEMBERS_COLS)
    in_col = "in_date" if "in_date" in df.columns else None
    out_col = "out_date" if "out_date" in df.columns else None
    if in_col is None:
        return pd.DataFrame(columns=MEMBERS_COLS)
    def _ymd8(val: object, empty: str) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return empty
        try:
            if pd.isna(val):
                return empty
        except Exception:
            pass
        s = str(val).replace("-", "").strip()[:8]
        if not s or s in {"nan", "None", "NaT", "<NA>"}:
            return empty
        return s

    df["ts_code"] = df["ts_code"].astype(str)
    df["_in"] = df[in_col].map(lambda v: _ymd8(v, "19000101"))
    df["_out"] = (
        df[out_col].map(lambda v: _ymd8(v, "99991231")) if out_col else "99991231"
    )

    anchors = {str(start)[:8], str(end)[:8]}
    for col in ("_in", "_out"):
        for v in df[col].astype(str):
            if v and v != "99991231" and start <= v <= end:
                anchors.add(v)
    rows: list[dict[str, Any]] = []
    for d in sorted(anchors):
        hit = df[(df["_in"] <= d) & (df["_out"] > d)]
        for code in hit["ts_code"].tolist():
            rows.append({"trade_date": d, "ts_code": code, "weight": pd.NA})
    return normalize_members(pd.DataFrame(rows))


def freeze_at_start(history: pd.DataFrame, start: str) -> pd.DataFrame:
    """Keep the last reconstitution on or before `start`, labeled as `start`."""
    hist = normalize_members(history)
    start = str(start)[:8]
    if hist.empty:
        return hist
    prior = hist[hist["trade_date"] <= start]
    if prior.empty:
        return pd.DataFrame(columns=MEMBERS_COLS)
    last = str(prior["trade_date"].max())
    snap = prior[prior["trade_date"] == last].copy()
    snap["trade_date"] = start
    return normalize_members(snap)


def universe_stats(members: pd.DataFrame) -> dict[str, Any]:
    df = normalize_members(members)
    if df.empty:
        return {
            "n_rows": 0,
            "n_snapshots": 0,
            "n_codes_union": 0,
            "n_codes_per_snapshot_mean": 0.0,
            "snapshot_dates": [],
        }
    sizes = df.groupby("trade_date")["ts_code"].nunique()
    dates = sorted(df["trade_date"].astype(str).unique().tolist())
    return {
        "n_rows": int(len(df)),
        "n_snapshots": int(len(dates)),
        "n_codes_union": int(df["ts_code"].nunique()),
        "n_codes_per_snapshot_mean": float(sizes.mean()) if len(sizes) else 0.0,
        "snapshot_dates": dates,
    }


def build_universe_mask(
    dates: list[str],
    codes: list[str],
    members: pd.DataFrame,
) -> pd.DataFrame:
    """Asof membership: each day uses the latest snapshot on or before that day."""
    dates = [str(d) for d in dates]
    codes = [str(c) for c in codes]
    members = normalize_members(members)
    mask = pd.DataFrame(False, index=dates, columns=codes)
    if members.empty or not dates or not codes:
        mask.index.name = "trade_date"
        return mask
    snaps = {
        d: set(g["ts_code"].astype(str)) for d, g in members.groupby("trade_date")
    }
    snap_dates = sorted(snaps)
    j = -1
    active: set[str] = set()
    for d in dates:
        while j + 1 < len(snap_dates) and snap_dates[j + 1] <= d:
            j += 1
            active = snaps[snap_dates[j]]
        if active:
            cols = [c for c in active if c in mask.columns]
            if cols:
                mask.loc[d, cols] = True
    mask.index.name = "trade_date"
    return mask


def classify_mode(members: pd.DataFrame, requested: str) -> str:
    stats = universe_stats(members)
    if requested == "snapshot":
        return "snapshot"
    if requested == "freeze_start" or stats["n_snapshots"] <= 1:
        return "freeze_start"
    return "pit"


def resolve_universe(
    *,
    start: str,
    end: str,
    history: pd.DataFrame | None,
    latest_snapshot: pd.DataFrame | None = None,
    cfg: ProjectConfig | None = None,
    provider: str | None = None,
    force_mode: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    pit: all reconstitutions in [start-lookback, end].
    freeze_start: last reconstitution on or before start, locked thereafter.
    snapshot: latest official file (lookahead; opt-in only).
    """
    policy = universe_policy(cfg)
    requested = str(force_mode or policy["mode"]).strip().lower()
    mode = requested if requested in {"pit", "freeze_start", "snapshot"} else policy["mode"]
    lookback = int(policy["lookback_days"])
    start = str(start)[:8]
    end = str(end)[:8]
    hist = normalize_members(history)
    latest = normalize_members(latest_snapshot)

    if mode == "snapshot":
        if latest.empty:
            raise RuntimeError("universe_policy.mode=snapshot but no CSIndex/latest members")
        # Honest label: membership is the file's own date, then asof-forward only.
        file_date = str(latest["trade_date"].iloc[0]) if len(latest) else start
        members = latest.assign(trade_date=start)
        meta = {
            "universe_mode": "snapshot",
            "provider": "csindex_latest",
            "note": (
                "Latest constituent file frozen at window start. This still uses "
                "today's names if the file is current — lookahead / survivorship."
            ),
            "file_date": file_date,
            **universe_stats(members),
        }
        return members, meta

    if hist.empty:
        raise RuntimeError(
            "Point-in-time CSI100 constituents require a verified historical "
            "reconstitution provider (for example Tushare, RQData, or a vetted local archive). "
            "Configure data_sources.providers.universe; do not downgrade to snapshot for production."
        )

    hist = hist[(hist["trade_date"] >= shift_yyyymmdd(start, -lookback)) & (hist["trade_date"] <= end)]
    if hist.empty:
        raise RuntimeError(
            f"Configured PIT provider returned no CSI100 reconstitutions in {shift_yyyymmdd(start, -lookback)}–{end}"
        )

    if mode == "freeze_start" or classify_mode(hist, "pit") == "freeze_start":
        members = freeze_at_start(hist, start)
        if members.empty:
            raise RuntimeError(f"No CSI100 snapshot on or before {start}")
        actual = "freeze_start"
        note = (
            "Frozen at the last reconstitution on or before window start. "
            "Not CSI100 thereafter — a fixed basket."
        )
    else:
        members = hist
        actual = "pit"
        note = (
            "Point-in-time CSI100: each day uses the latest reconstitution "
            "on or before that day. Bars should cover the union of members."
        )
        if str(members["trade_date"].min()) > start:
            raise RuntimeError(
                f"No CSI100 reconstitution on or before {start}; "
                "increase universe_policy.lookback_days (need the previous Jun/Dec review)."
            )

    meta = {
        "universe_mode": actual,
        "provider": provider or "verified_provider",
        "index_code": policy["index_code"],
        "lookback_days": lookback,
        "requested_mode": mode,
        "note": note,
        **universe_stats(members),
    }
    return members, meta
