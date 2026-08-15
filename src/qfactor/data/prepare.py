from __future__ import annotations

"""Fetch, keep, and check research bars before any factor production.

`loop` / supervisor do not download bars by themselves. This module is the
gate in front of mining: inspect coverage against a configured window, sync
only when that window is incomplete, then re-check layered contracts.
`mining_allowed` requires a covered window and a passing research contract.
Candidate / release stay fail-closed and never unlock research mining.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from qfactor.agent.experiments import factor_contract_readiness
from qfactor.data.dataset import DataService, codes_covering_window
from qfactor.data.universe import shift_yyyymmdd
from qfactor.settings import ProjectConfig, get_project_config

DEFAULT_START = "20200101"
DEFAULT_SOURCE = "baostock"
DEFAULT_MIN_COVERING_NAMES = 50
HOLIDAY_SLACK_DAYS = 7
CALENDAR_SPAN_SLACK_DAYS = 10

SNAPSHOT_UNIVERSE_MARKERS = (
    "do not downgrade to snapshot",
    "no csi100 reconstitution",
    "returned no csi100 reconstitutions",
    "point-in-time csi100 constituents require",
    "need those apis for pit",
    "tushare_token",
    "permission. pit csi100",
    "universe_policy.mode=snapshot but no",
)


def today_yyyymmdd(tz: str = "Asia/Shanghai") -> str:
    try:
        return datetime.now(ZoneInfo(tz)).strftime("%Y%m%d")
    except Exception:
        return datetime.now().strftime("%Y%m%d")


def is_snapshot_universe_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in SNAPSHOT_UNIVERSE_MARKERS)


def data_prepare_settings(
    cfg: ProjectConfig | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    source: str | None = None,
    allow_snapshot_universe: bool | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_project_config()
    raw = cfg.project.get("data_prepare") or {}
    tz = str(cfg.project.get("timezone") or "Asia/Shanghai")
    resolved_end = str(end if end is not None else raw.get("end") or "").strip()
    if not resolved_end:
        resolved_end = today_yyyymmdd(tz)
    allow = raw.get("allow_snapshot_universe", True)
    if allow_snapshot_universe is not None:
        allow = allow_snapshot_universe
    return {
        "start": str(start or raw.get("start") or DEFAULT_START)[:8],
        "end": resolved_end[:8],
        "source": str(source or raw.get("source") or DEFAULT_SOURCE),
        "allow_snapshot_universe": bool(allow),
        "refresh_if_incomplete": bool(raw.get("refresh_if_incomplete", True)),
        "require_research_contract": bool(raw.get("require_research_contract", True)),
        "require_window_coverage": bool(raw.get("require_window_coverage", True)),
        "min_covering_names": int(raw.get("min_covering_names") or DEFAULT_MIN_COVERING_NAMES),
        "timezone": tz,
    }


@dataclass
class DataPrepareResult:
    inspected: bool = True
    synced: bool = False
    skipped_sync: bool = True
    reason: str = ""
    start: str = ""
    end: str = ""
    source: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)
    contracts: dict[str, Any] = field(default_factory=dict)
    mining_allowed: bool = False
    block_reason: str | None = None
    used_snapshot_universe: bool = False
    allow_snapshot_universe: bool = False
    sync_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DataPrepareService:
    """Inspect bar coverage, sync if incomplete, then gate research mining."""

    def __init__(
        self,
        cfg: ProjectConfig | None = None,
        data: DataService | None = None,
    ):
        self.cfg = cfg or get_project_config()
        self.data = data or DataService(self.cfg)

    def inspect(
        self,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        settings = data_prepare_settings(self.cfg, start=start, end=end)
        start = settings["start"]
        end = settings["end"]
        status = self.data.status()
        meta = status.get("meta") or {}
        covering: list[str] = []
        n_codes = 0
        panel_start = meta.get("start")
        panel_end = meta.get("end")
        load_error: str | None = None
        try:
            bars = self.data.load_bars()
        except Exception as exc:
            bars = None
            load_error = str(exc)
        if bars is not None and not getattr(bars, "empty", True):
            covering = sorted(self._covering_codes(bars, start, end))
            n_codes = int(pd.Series(bars["ts_code"]).astype(str).nunique())
            dates = pd.Series(bars["trade_date"]).astype(str).str.replace("-", "", regex=False).str[:8]
            panel_start = str(dates.min())
            panel_end = str(dates.max())
        cov_start, cov_end, cov_method = self._coverage_bounds(start, end)
        window_covered = len(covering) >= int(settings["min_covering_names"])
        return {
            "start": start,
            "end": end,
            "coverage_start": cov_start,
            "coverage_end": cov_end,
            "coverage_method": cov_method,
            "has_bars": bool(status.get("has_bars")),
            "data_version": status.get("data_version"),
            "meta_start": meta.get("start"),
            "meta_end": meta.get("end"),
            "panel_start": panel_start,
            "panel_end": panel_end,
            "n_codes": n_codes,
            "n_covering": len(covering),
            "min_covering_names": int(settings["min_covering_names"]),
            "covering_sample": covering[:12],
            "window_covered": window_covered,
            "universe_mode": meta.get("universe_mode"),
            "circ_mv_source": meta.get("circ_mv_source"),
            "load_error": load_error,
        }

    def ensure_research_ready(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        source: str | None = None,
        sync: bool | None = None,
        allow_snapshot_universe: bool | None = None,
        force_sync: bool = False,
    ) -> DataPrepareResult:
        settings = data_prepare_settings(
            self.cfg,
            start=start,
            end=end,
            source=source,
            allow_snapshot_universe=allow_snapshot_universe,
        )
        coverage = self.inspect(settings["start"], settings["end"])
        should_sync = bool(force_sync) or (
            sync is True
            or (
                sync is not False
                and settings["refresh_if_incomplete"]
                and not coverage["window_covered"]
            )
        )
        synced = False
        used_snapshot = False
        sync_error: str | None = None
        reason = "window_covered" if coverage["window_covered"] else "window_incomplete"
        if should_sync:
            try:
                _, used_snapshot = self._sync_with_policy(settings)
                synced = True
                reason = "synced_snapshot" if used_snapshot else "synced"
                coverage = self.inspect(settings["start"], settings["end"])
                if not coverage["window_covered"]:
                    reason = "synced_but_still_incomplete"
            except Exception as exc:
                sync_error = str(exc)
                reason = "sync_failed"
        contracts = factor_contract_readiness(self.cfg)
        research = contracts.get("research") or {}
        candidate = contracts.get("candidate") or {}
        release = contracts.get("release") or {}
        contract_summary = {
            "research": research.get("state"),
            "candidate": candidate.get("state"),
            "release": release.get("state"),
            "research_issues": list(research.get("issues") or []),
            "candidate_issues": list(candidate.get("issues") or []),
            "release_issues": list(release.get("issues") or []),
            "data_version": contracts.get("data_version"),
        }
        mining_allowed = True
        blocks: list[str] = []
        if settings["require_window_coverage"] and not coverage["window_covered"]:
            mining_allowed = False
            blocks.append(
                f"window_incomplete:{coverage['n_covering']}<{coverage['min_covering_names']}"
            )
        if settings["require_research_contract"] and research.get("state") != "passed":
            mining_allowed = False
            issues = contract_summary["research_issues"]
            blocks.append(", ".join(issues) if issues else "research_contract_blocked")
        if sync_error and not coverage["window_covered"]:
            mining_allowed = False
            blocks.append(f"sync_failed:{sync_error}")
        block_reason = "; ".join(blocks) if blocks else None
        return DataPrepareResult(
            inspected=True,
            synced=synced,
            skipped_sync=not should_sync,
            reason=reason,
            start=settings["start"],
            end=settings["end"],
            source=settings["source"],
            coverage=coverage,
            contracts=contract_summary,
            mining_allowed=mining_allowed,
            block_reason=block_reason,
            used_snapshot_universe=used_snapshot,
            allow_snapshot_universe=bool(settings["allow_snapshot_universe"]),
            sync_error=sync_error,
        )

    def _coverage_bounds(self, start: str, end: str) -> tuple[str, str, str]:
        opened = self._calendar_open_window(start, end)
        if opened is not None:
            return opened[0], opened[1], "calendar"
        return (
            shift_yyyymmdd(start, HOLIDAY_SLACK_DAYS),
            shift_yyyymmdd(end, -HOLIDAY_SLACK_DAYS),
            "holiday_slack",
        )

    def _covering_codes(self, bars: pd.DataFrame, start: str, end: str) -> set[str]:
        cov_start, cov_end, _ = self._coverage_bounds(start, end)
        return codes_covering_window(bars, cov_start, cov_end)

    def _calendar_open_window(self, start: str, end: str) -> tuple[str, str] | None:
        path = getattr(self.data, "calendar_path", None)
        if path is None:
            return None
        try:
            cal_path = Path(path)
            if not cal_path.exists():
                return None
            cal = pd.read_parquet(cal_path)
            if cal.empty or "cal_date" not in cal.columns:
                return None
            use = cal.copy()
            use["cal_date"] = use["cal_date"].astype(str).str.replace("-", "", regex=False).str[:8]
            if "is_open" in use.columns:
                use = use.loc[use["is_open"] == 1]
            opens = use.loc[use["cal_date"].between(str(start)[:8], str(end)[:8]), "cal_date"]
            if opens.empty:
                return None
            first = str(opens.min())[:8]
            last = str(opens.max())[:8]
            if first <= shift_yyyymmdd(start, CALENDAR_SPAN_SLACK_DAYS) and last >= shift_yyyymmdd(
                end, -CALENDAR_SPAN_SLACK_DAYS
            ):
                return first, last
        except Exception:
            return None
        return None

    def _sync_with_policy(self, settings: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        start = settings["start"]
        end = settings["end"]
        source = settings["source"]
        try:
            meta = self.data.sync(
                start,
                end,
                source=source,  # type: ignore[arg-type]
                allow_snapshot_universe=False,
            )
            return meta, False
        except Exception as exc:
            if settings["allow_snapshot_universe"] and is_snapshot_universe_error(exc):
                print(
                    "[prepare] PIT universe unavailable; retrying research bars "
                    "with snapshot universe (not a candidate contract)",
                    flush=True,
                )
                meta = self.data.sync(
                    start,
                    end,
                    source=source,  # type: ignore[arg-type]
                    allow_snapshot_universe=True,
                )
                return meta, True
            raise
