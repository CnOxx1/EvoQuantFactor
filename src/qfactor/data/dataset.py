from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from qfactor.data.akshare_adapter import AkshareAdapter
from qfactor.data.baostock_adapter import BaostockAdapter, bs_session
from qfactor.data.base import DataAdapter
from qfactor.data.csindex import fetch_csindex_members
from qfactor.data.quality import check_daily_panel
from qfactor.data.tushare_adapter import TushareAdapter, adapter_kwargs_from_config
from qfactor.settings import ProjectConfig, get_project_config, get_settings


def overlay_daily_basic(panel: pd.DataFrame, basic: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Prefer Tushare daily_basic circ_mv / turnover; keep bar estimates as fallback."""
    out = panel.copy()
    if "circ_mv" not in out.columns:
        out["circ_mv"] = np.nan
    if "turnover_rate" not in out.columns:
        out["turnover_rate"] = np.nan
    info = {
        "circ_mv_source": "estimated" if out["circ_mv"].notna().any() else "none",
        "daily_basic_coverage": 0.0,
    }
    if basic is None or basic.empty:
        return out, info
    use = basic.copy()
    use["trade_date"] = use["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    use["ts_code"] = use["ts_code"].astype(str)
    cols = [c for c in ("trade_date", "ts_code", "circ_mv", "turnover_rate") if c in use.columns]
    use = use[cols].drop_duplicates(["trade_date", "ts_code"])
    renamed = use.rename(
        columns={"circ_mv": "circ_mv_ts", "turnover_rate": "turnover_rate_ts"}
    )
    out["trade_date"] = out["trade_date"].astype(str)
    out["ts_code"] = out["ts_code"].astype(str)
    out = out.merge(renamed, on=["trade_date", "ts_code"], how="left")
    vendor = float(out["circ_mv_ts"].notna().mean()) if "circ_mv_ts" in out.columns else 0.0
    if "circ_mv_ts" in out.columns:
        out["circ_mv"] = out["circ_mv_ts"].fillna(out["circ_mv"])
    if "turnover_rate_ts" in out.columns:
        out["turnover_rate"] = out["turnover_rate_ts"].fillna(out["turnover_rate"])
    out = out.drop(columns=["circ_mv_ts", "turnover_rate_ts"], errors="ignore")
    info["daily_basic_coverage"] = vendor
    if vendor >= 0.3:
        info["circ_mv_source"] = "tushare_daily_basic"
    elif out["circ_mv"].notna().any():
        info["circ_mv_source"] = "estimated"
    else:
        info["circ_mv_source"] = "none"
    return out, info


def _universe_limitations(
    umeta: dict,
    existing: list | None = None,
    circ_mv_source: str | None = None,
) -> list[str]:
    mode = str(umeta.get("universe_mode") or "")
    notes = [str(umeta.get("note") or "").strip()] if umeta.get("note") else []
    if mode == "pit":
        notes.append("Universe is point-in-time CSI100 reconstitutions (Tushare)")
    elif mode == "freeze_start":
        notes.append("Universe frozen at first reconstitution on/before window start")
    elif mode == "snapshot":
        notes.append("CSIndex file is latest snapshot (not full historical reconstitution)")
    src = str(circ_mv_source or "").strip()
    if src == "tushare_daily_basic":
        circ = "circ_mv from Tushare daily_basic"
    else:
        circ = "circ_mv estimated from amount/turnover when vendor cap unavailable"
    extra = [
        x
        for x in (existing or [])
        if "snapshot" not in str(x).lower()
        and "reconstitution" not in str(x).lower()
        and "circ_mv" not in str(x).lower()
    ]
    out: list[str] = []
    for n in notes + extra:
        if n and n not in out:
            out.append(n)
    if circ not in out:
        out.append(circ)
    return out


def build_adapter(
    source: Literal["tushare", "akshare", "baostock", "auto"] = "auto",
    cfg: ProjectConfig | None = None,
) -> DataAdapter:
    cfg = cfg or get_project_config()
    dsc = cfg.data_sources
    if source == "auto":
        if get_settings().tushare_token:
            source = "tushare"  # type: ignore[assignment]
        else:
            source = str(dsc.get("fallback", "baostock"))  # type: ignore[assignment]
    if source == "tushare":
        return TushareAdapter(**adapter_kwargs_from_config(dsc))
    if source == "baostock":
        return BaostockAdapter()
    if source == "akshare":
        ak_cfg = dsc.get("akshare", {})
        return AkshareAdapter(index_symbol=ak_cfg.get("index_symbol", "sh000903"))
    raise ValueError(f"Unknown source: {source}")


class DataService:
    """Unified data access for CLI / Agent / Web UI."""

    def __init__(self, cfg: ProjectConfig | None = None, adapter: DataAdapter | None = None):
        self.cfg = cfg or get_project_config()
        self.cfg.ensure_dirs()
        self.adapter = adapter

    def _adapter(
        self, source: Literal["tushare", "akshare", "baostock", "auto"] = "auto"
    ) -> DataAdapter:
        return self.adapter or build_adapter(source, self.cfg)

    def _tushare_universe_adapter(self) -> TushareAdapter | None:
        if not get_settings().tushare_token:
            return None
        try:
            return TushareAdapter(**adapter_kwargs_from_config(self.cfg.data_sources))
        except Exception as e:
            print(f"[sync] Tushare universe adapter failed: {e}", flush=True)
            return None

    def _load_universe_history(self, start: str, end: str) -> pd.DataFrame:
        from qfactor.data.universe import shift_yyyymmdd, universe_policy

        ts = self._tushare_universe_adapter()
        if ts is None:
            return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])
        lookback = int(universe_policy(self.cfg)["lookback_days"])
        hist_start = shift_yyyymmdd(start, -lookback)
        print(f"[sync] fetching PIT CSI100 {hist_start}–{end}", flush=True)
        return ts.fetch_index_members_history(hist_start, end)

    def _latest_csindex(self) -> pd.DataFrame:
        try:
            return fetch_csindex_members("000903")
        except Exception as e:
            print(f"[sync] CSIndex latest failed: {e}", flush=True)
            return pd.DataFrame(columns=["trade_date", "ts_code", "weight"])

    def resolve_and_persist_universe(self, start: str, end: str) -> tuple[pd.DataFrame, dict]:
        from qfactor.data.universe import resolve_universe, universe_policy

        policy = universe_policy(self.cfg)
        history = None
        latest = None
        if policy["mode"] == "snapshot":
            latest = self._latest_csindex()
        else:
            history = self._load_universe_history(start, end)
        members, umeta = resolve_universe(
            start=start,
            end=end,
            history=history,
            latest_snapshot=latest,
            cfg=self.cfg,
        )
        self.universe_path.parent.mkdir(parents=True, exist_ok=True)
        members.to_parquet(self.universe_path, index=False)
        print(
            f"[sync] universe mode={umeta.get('universe_mode')} "
            f"snapshots={umeta.get('n_snapshots')} union={umeta.get('n_codes_union')}",
            flush=True,
        )
        return members, umeta

    def sync_universe(self, start: str, end: str) -> dict:
        """Refresh PIT constituents without re-downloading bars."""
        members, umeta = self.resolve_and_persist_universe(start, end)
        bars_codes: set[str] = set()
        try:
            bars = self.load_bars()
            bars_codes = set(bars["ts_code"].astype(str).unique())
        except Exception:
            bars_codes = set()
        union = set(members["ts_code"].astype(str).unique())
        missing = sorted(union - bars_codes)
        umeta["n_codes_missing_bars"] = len(missing)
        umeta["missing_bars_sample"] = missing[:20]
        if missing:
            print(
                f"[sync] {len(missing)} PIT names have no bars; re-run sync-data to download the union",
                flush=True,
            )
        meta_path = self.cfg.path("data_processed") / "data_version.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        meta["members_provider"] = umeta
        meta["universe_mode"] = umeta.get("universe_mode")
        meta["limitations"] = _universe_limitations(umeta, meta.get("limitations") or [])
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            from qfactor.db.repo import Database

            db = Database()
            db.replace_universe(self.cfg.universe, members)
            db.save_data_version(meta)
        except Exception as e:
            print(f"[sync] universe db write failed: {e}", flush=True)
            umeta["database_error"] = str(e)
        return {**umeta, "start": start, "end": end}

    @property
    def bars_path(self) -> Path:
        return self.cfg.path("data_processed") / "bars" / "daily" / "bars.parquet"

    @property
    def universe_path(self) -> Path:
        return (
            self.cfg.path("data_processed")
            / "universe"
            / self.cfg.universe
            / "members.parquet"
        )

    @property
    def industry_path(self) -> Path:
        return self.cfg.path("data_processed") / "meta" / "industry.parquet"

    @property
    def calendar_path(self) -> Path:
        return self.cfg.path("data_processed") / "calendar" / "trade_cal.parquet"

    def sync(
        self,
        start: str,
        end: str,
        source: Literal["tushare", "akshare", "baostock", "auto"] = "auto",
        max_names: int | None = None,
    ) -> dict:
        adapter = self._adapter(source)
        if adapter.name == "baostock":
            with bs_session() as bs:
                adapter.bind_session(bs)  # type: ignore[attr-defined]
                return self._sync_with_adapter(adapter, start, end, max_names=max_names)
        return self._sync_with_adapter(adapter, start, end, max_names=max_names)

    def _fetch_trade_calendar(
        self, adapter: DataAdapter, start: str, end: str
    ) -> tuple[pd.DataFrame, str]:
        """Production calendar is Baostock trading days, never Tushare trade_cal."""
        if adapter.name == "baostock":
            cal = adapter.fetch_trade_calendar(start, end)
            return cal, "baostock"
        print("[sync] calendar source=baostock (not Tushare trade_cal)", flush=True)
        cal = BaostockAdapter().fetch_trade_calendar(start, end)
        return cal, "baostock"

    def _sync_with_adapter(
        self,
        adapter: DataAdapter,
        start: str,
        end: str,
        max_names: int | None,
    ) -> dict:
        calendar, calendar_source = self._fetch_trade_calendar(adapter, start, end)
        self.calendar_path.parent.mkdir(parents=True, exist_ok=True)
        calendar.to_parquet(self.calendar_path, index=False)
        open_dates = (
            calendar.loc[calendar["is_open"] == 1, "cal_date"].astype(str).tolist()
        )
        if not open_dates:
            raise RuntimeError("No open trade dates in range")

        members, umeta = self.resolve_and_persist_universe(start, end)
        member_meta_info = umeta

        codes = sorted(members["ts_code"].dropna().astype(str).unique().tolist())
        if max_names is not None:
            codes = codes[:max_names]

        have: set[str] = set()
        existing_panel: pd.DataFrame | None = None
        if self.bars_path.exists():
            try:
                prev = pd.read_parquet(self.bars_path)
                prev = prev[
                    prev["ts_code"].astype(str).isin(codes)
                    & prev["trade_date"].astype(str).between(str(start), str(end))
                ]
                if not prev.empty:
                    existing_panel = prev
                    have = set(prev["ts_code"].astype(str).unique())
            except Exception as e:
                print(f"[sync] reuse existing bars failed: {e}", flush=True)
        need = [c for c in codes if c not in have]
        print(
            f"[sync] union={len(codes)} reuse={len(have)} fetch={len(need)}",
            flush=True,
        )

        frames: list[pd.DataFrame] = []
        if existing_panel is not None and not existing_panel.empty:
            frames.append(existing_panel)
        failed: list[str] = []
        for i, code in enumerate(need, 1):
            try:
                bars = adapter.fetch_daily_bars(code, start, end)
                if bars.empty:
                    failed.append(code)
                    print(f"[sync] {i}/{len(need)} {code} empty", flush=True)
                    continue
                if "adj_factor" not in bars.columns:
                    bars["adj_factor"] = 1.0
                if "turnover_rate" not in bars.columns:
                    bars["turnover_rate"] = np.nan
                if "circ_mv" not in bars.columns:
                    bars["circ_mv"] = np.nan
                frames.append(bars)
                print(f"[sync] {i}/{len(need)} {code} rows={len(bars)}", flush=True)
            except Exception as e:
                failed.append(code)
                print(f"[sync] {i}/{len(need)} {code} FAILED: {e}", flush=True)

        if not frames:
            raise RuntimeError("No daily bars downloaded")

        panel = pd.concat(frames, ignore_index=True)
        panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        panel["adj_factor"] = panel.groupby("ts_code")["adj_factor"].ffill().fillna(1.0)
        parts: list[pd.DataFrame] = []
        for _, g in panel.groupby("ts_code", sort=False):
            g = g.copy()
            last = float(g["adj_factor"].iloc[-1]) if len(g) else 1.0
            if last == 0:
                last = 1.0
            g["close_adj"] = g["close"] * g["adj_factor"] / last
            g["ret_1d"] = g["close_adj"].pct_change()
            parts.append(g)
        panel = pd.concat(parts, ignore_index=True)

        basic_info = {"circ_mv_source": "estimated", "daily_basic_coverage": 0.0}
        ts = self._tushare_universe_adapter()
        if ts is not None:
            basic_frames: list[pd.DataFrame] = []
            for i, code in enumerate(codes, 1):
                try:
                    basic = ts.fetch_daily_basic(code, start, end)
                    if basic is not None and not basic.empty:
                        basic_frames.append(basic)
                    print(f"[sync] daily_basic {i}/{len(codes)} {code}", flush=True)
                except Exception as e:
                    print(f"[sync] daily_basic {code} FAILED: {e}", flush=True)
            stacked = (
                pd.concat(basic_frames, ignore_index=True) if basic_frames else pd.DataFrame()
            )
            panel, basic_info = overlay_daily_basic(panel, stacked)
        else:
            panel, basic_info = overlay_daily_basic(panel, pd.DataFrame())
            print("[sync] no TUSHARE_TOKEN; circ_mv stays estimated", flush=True)

        # Industry map for diagnostics / future neutralize
        industry = pd.DataFrame(columns=["ts_code", "industry", "industry_source"])
        if hasattr(adapter, "fetch_industry_map"):
            try:
                industry = adapter.fetch_industry_map(  # type: ignore[attr-defined]
                    sorted(panel["ts_code"].unique().tolist())
                )
                self.industry_path.parent.mkdir(parents=True, exist_ok=True)
                industry.to_parquet(self.industry_path, index=False)
                if not industry.empty:
                    panel = panel.merge(industry, on="ts_code", how="left")
            except Exception as e:
                print(f"[sync] industry map failed: {e}", flush=True)

        self.bars_path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(self.bars_path, index=False)

        report = check_daily_panel(panel)
        meta = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "source": adapter.name,
            "members_provider": member_meta_info,
            "start": start,
            "end": end,
            "universe": self.cfg.universe,
            "n_codes_requested": len(codes),
            "n_codes_ok": int(panel["ts_code"].nunique()),
            "n_codes_reused": len(have),
            "n_codes_fetched": len(need),
            "n_codes_failed": len(failed),
            "failed_codes": failed[:50],
            "has_industry": bool(len(industry)),
            "has_circ_mv": bool(panel["circ_mv"].notna().any())
            if "circ_mv" in panel.columns
            else False,
            "circ_mv_source": basic_info.get("circ_mv_source"),
            "daily_basic_coverage": basic_info.get("daily_basic_coverage"),
            "quality": report.to_dict(),
            "data_version": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "calendar_source": calendar_source,
            "universe_mode": umeta.get("universe_mode"),
            "limitations": _universe_limitations(
                umeta, circ_mv_source=str(basic_info.get("circ_mv_source") or "")
            ),
        }
        meta_path = self.cfg.path("data_processed") / "data_version.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        qdir = self.cfg.root / "data" / "quality_reports"
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / f"sync_{meta['data_version']}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Persist into SQLite for downstream modules
        try:
            from qfactor.db.repo import Database

            db = Database()
            job_id = db.create_job(
                "sync", {"start": start, "end": end, "source": adapter.name}
            )
            db.replace_calendar(calendar)
            db.replace_universe(self.cfg.universe, members)
            db.replace_bars_for_sync(
                panel,
                ts_codes=sorted(panel["ts_code"].astype(str).unique().tolist()),
                start=start,
                end=end,
            )
            if not industry.empty:
                db.replace_industry(industry)
            db.save_data_version(meta)
            db.finish_job(job_id, "done", result=meta)
            meta["database"] = "sqlite"
            meta["db_status"] = db.status()
        except Exception as e:
            print(f"[sync] database write failed: {e}", flush=True)
            meta["database_error"] = str(e)
        return meta

    def load_bars(self) -> pd.DataFrame:
        """Prefer DB when its data_version matches current sync meta; else parquet."""
        file_ver = None
        meta_path = self.cfg.path("data_processed") / "data_version.json"
        if meta_path.exists():
            try:
                file_ver = json.loads(meta_path.read_text(encoding="utf-8")).get(
                    "data_version"
                )
            except Exception:
                file_ver = None
        try:
            from qfactor.db.repo import Database

            db = Database()
            cur = db.current_data_version() or {}
            db_ver = cur.get("data_version")
            df = db.load_bars()
            if not df.empty and (not file_ver or db_ver == file_ver):
                return df
        except Exception as e:
            print(f"[data] load_bars db fallback: {e}", flush=True)
        if not self.bars_path.exists():
            raise FileNotFoundError(
                f"Missing bars in DB/parquet. Run: qfactor sync-data --start ... --end ..."
            )
        return pd.read_parquet(self.bars_path)

    def load_industry(self) -> pd.DataFrame:
        try:
            from qfactor.db.repo import Database

            df = Database().load_industry()
            if not df.empty:
                return df
        except Exception:
            pass
        if not self.industry_path.exists():
            return pd.DataFrame(columns=["ts_code", "industry"])
        return pd.read_parquet(self.industry_path)

    def load_universe_mask(self) -> pd.DataFrame:
        from qfactor.data.universe import build_universe_mask

        members = None
        try:
            from qfactor.db.repo import Database

            members = Database().load_universe(self.cfg.universe)
        except Exception:
            members = None
        if members is None or members.empty:
            if not self.universe_path.exists():
                raise FileNotFoundError(self.universe_path)
            members = pd.read_parquet(self.universe_path)
        bars = self.load_bars()
        dates = sorted(bars["trade_date"].astype(str).unique())
        codes = sorted(bars["ts_code"].astype(str).unique())
        return build_universe_mask(dates, codes, members)

    def data_version(self) -> str | None:
        try:
            from qfactor.db.repo import Database

            cur = Database().current_data_version()
            if cur:
                return cur.get("data_version")
        except Exception:
            pass
        p = self.cfg.path("data_processed") / "data_version.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8")).get("data_version")

    def status(self) -> dict:
        meta_path = self.cfg.path("data_processed") / "data_version.json"
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        out = {
            "has_bars": self.bars_path.exists(),
            "has_universe": self.universe_path.exists(),
            "has_industry": self.industry_path.exists(),
            "data_version": meta.get("data_version"),
            "meta": meta,
        }
        try:
            from qfactor.db.models import db_path
            from qfactor.db.repo import Database

            db = Database()
            out["database"] = {
                "path": str(db_path(self.cfg)),
                **db.status(),
            }
            if db.status().get("n_bars", 0) > 0:
                out["has_bars"] = True
            if db.status().get("n_universe_rows", 0) > 0:
                out["has_universe"] = True
            if db.status().get("n_industry", 0) > 0:
                out["has_industry"] = True
            cur = db.current_data_version()
            if cur:
                out["data_version"] = cur.get("data_version")
                out["meta"] = cur
        except Exception as e:
            out["database_error"] = str(e)
        return out
