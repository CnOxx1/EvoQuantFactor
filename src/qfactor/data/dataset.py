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
from qfactor.data.csindex import fetch_csindex_members, member_meta
from qfactor.data.quality import check_daily_panel
from qfactor.data.tushare_adapter import TushareAdapter, adapter_kwargs_from_config
from qfactor.settings import ProjectConfig, get_project_config, get_settings


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
        # Preferred no-Tushare path: CSIndex official members + Baostock bars.
        use_csindex = adapter.name in {"baostock", "akshare"} or (
            source in {"auto", "baostock"} and not get_settings().tushare_token
        )

        if adapter.name == "baostock":
            with bs_session() as bs:
                adapter.bind_session(bs)  # type: ignore[attr-defined]
                return self._sync_with_adapter(
                    adapter, start, end, max_names=max_names, use_csindex=use_csindex
                )
        return self._sync_with_adapter(
            adapter, start, end, max_names=max_names, use_csindex=use_csindex
        )

    def _sync_with_adapter(
        self,
        adapter: DataAdapter,
        start: str,
        end: str,
        max_names: int | None,
        use_csindex: bool,
    ) -> dict:
        calendar = adapter.fetch_trade_calendar(start, end)
        self.calendar_path.parent.mkdir(parents=True, exist_ok=True)
        calendar.to_parquet(self.calendar_path, index=False)
        open_dates = (
            calendar.loc[calendar["is_open"] == 1, "cal_date"].astype(str).tolist()
        )
        if not open_dates:
            raise RuntimeError("No open trade dates in range")

        member_meta_info = {}
        if use_csindex:
            try:
                latest = fetch_csindex_members("000903")
                member_meta_info = member_meta()
                # Stamp latest official list onto research window endpoints for asof mask.
                members = pd.concat(
                    [
                        latest.assign(trade_date=open_dates[0]),
                        latest.assign(trade_date=open_dates[-1]),
                    ],
                    ignore_index=True,
                )
                print(
                    f"[sync] CSIndex members={len(latest)} file_date={latest['trade_date'].iloc[0]}",
                    flush=True,
                )
            except Exception as e:
                print(f"[sync] CSIndex failed ({e}), fallback adapter members", flush=True)
                members = adapter.fetch_index_members(open_dates[-1])
                member_meta_info = {"provider": adapter.name, "note": "fallback members"}
        else:
            latest = adapter.fetch_index_members(open_dates[-1])
            # Stamp window start+end so asof mask is non-empty for full history.
            members = pd.concat(
                [
                    latest.assign(trade_date=open_dates[0]),
                    latest.assign(trade_date=open_dates[-1]),
                ],
                ignore_index=True,
            )
            member_meta_info = {
                "provider": adapter.name,
                "note": "latest members stamped to start/end of sync window",
            }

        self.universe_path.parent.mkdir(parents=True, exist_ok=True)
        members.to_parquet(self.universe_path, index=False)

        codes = sorted(members["ts_code"].dropna().astype(str).unique().tolist())
        if max_names is not None:
            codes = codes[:max_names]

        frames: list[pd.DataFrame] = []
        failed: list[str] = []
        for i, code in enumerate(codes, 1):
            try:
                bars = adapter.fetch_daily_bars(code, start, end)
                if bars.empty:
                    failed.append(code)
                    print(f"[sync] {i}/{len(codes)} {code} empty", flush=True)
                    continue
                if "adj_factor" not in bars.columns:
                    bars["adj_factor"] = 1.0
                if "turnover_rate" not in bars.columns:
                    bars["turnover_rate"] = np.nan
                if "circ_mv" not in bars.columns:
                    bars["circ_mv"] = np.nan
                frames.append(bars)
                print(f"[sync] {i}/{len(codes)} {code} rows={len(bars)}", flush=True)
            except Exception as e:
                failed.append(code)
                print(f"[sync] {i}/{len(codes)} {code} FAILED: {e}", flush=True)

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
            "n_codes_failed": len(failed),
            "failed_codes": failed[:50],
            "has_industry": bool(len(industry)),
            "has_circ_mv": bool(panel["circ_mv"].notna().any())
            if "circ_mv" in panel.columns
            else False,
            "quality": report.to_dict(),
            "data_version": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "limitations": [
                "CSIndex file is latest snapshot (not full historical reconstitution)",
                "circ_mv estimated from amount/turnover when vendor cap unavailable",
            ],
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
        members = members.copy()
        members["trade_date"] = members["trade_date"].astype(str)
        snaps = {
            d: set(g["ts_code"].astype(str)) for d, g in members.groupby("trade_date")
        }
        snap_dates = sorted(snaps)
        mask = pd.DataFrame(False, index=dates, columns=codes)
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
