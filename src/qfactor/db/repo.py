from __future__ import annotations

import json
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from qfactor.db.models import (
    DailyBar,
    DataVersion,
    FactorAcceptance,
    FactorRelease,
    FactorReport,
    FactorRow,
    IndustryMap,
    JobRun,
    LibraryOpLog,
    LoopCheckpoint,
    ResearchExperiment,
    ResearchTrial,
    TradeCalendar,
    UniverseMember,
    get_session_factory,
    init_db,
    utc_now,
)


class Database:
    """Project SQLite access layer for market data + factor/ops metadata."""

    def __init__(self, db_url: str | None = None):
        self.db_url = db_url
        init_db(db_url)
        self.Session = get_session_factory(db_url)

    def replace_calendar(self, df: pd.DataFrame) -> int:
        rows = [
            {"cal_date": str(r.cal_date), "is_open": int(r.is_open)}
            for r in df.itertuples(index=False)
        ]
        with self.Session() as s:
            s.execute(delete(TradeCalendar))
            if rows:
                s.execute(sqlite_insert(TradeCalendar), rows)
            s.commit()
        return len(rows)

    def replace_universe(self, universe: str, df: pd.DataFrame) -> int:
        cols = set(df.columns)
        rows = []
        for r in df.itertuples(index=False):
            weight = getattr(r, "weight", None) if "weight" in cols else None
            rows.append(
                {
                    "universe": universe,
                    "trade_date": str(getattr(r, "trade_date")),
                    "ts_code": str(getattr(r, "ts_code")),
                    "name": str(getattr(r, "name")) if "name" in cols else None,
                    "weight": float(weight) if weight is not None and pd.notna(weight) else None,
                    "source": str(getattr(r, "source")) if "source" in cols else None,
                    "file_date": str(getattr(r, "file_date"))
                    if "file_date" in cols and pd.notna(getattr(r, "file_date", None))
                    else None,
                }
            )
        with self.Session() as s:
            s.execute(delete(UniverseMember).where(UniverseMember.universe == universe))
            if rows:
                s.execute(sqlite_insert(UniverseMember), rows)
            s.commit()
        return len(rows)

    def replace_bars_for_sync(
        self,
        df: pd.DataFrame,
        ts_codes: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> int:
        """Delete overlapping range then upsert — keeps DB aligned with sync window."""
        with self.Session() as s:
            q = delete(DailyBar)
            if ts_codes:
                q = q.where(DailyBar.ts_code.in_(list(ts_codes)))
            if start:
                q = q.where(DailyBar.trade_date >= str(start))
            if end:
                q = q.where(DailyBar.trade_date <= str(end))
            if ts_codes or start or end:
                s.execute(q)
            s.commit()
        return self.upsert_bars(df)

    def upsert_bars(self, df: pd.DataFrame, chunk: int = 40) -> int:
        """chunk kept small: SQLite caps ~999 bind vars per statement."""
        if df.empty:
            return 0
        keep = [
            c
            for c in [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "vol",
                "amount",
                "adj_factor",
                "close_adj",
                "ret_1d",
                "turnover_rate",
                "circ_mv",
                "pe_ttm",
                "pb",
                "industry",
                "is_st",
                "is_suspended",
                "limit_up",
                "limit_down",
                "free_float_shares",
                "adv_20d",
                "corporate_action",
                "adj_factor_vendor",
            ]
            if c in df.columns
        ]
        # ensure variable count stays under sqlite limit
        max_rows = max(1, 900 // max(len(keep), 1))
        chunk = min(chunk, max_rows)
        data = df[keep].copy()
        data["ts_code"] = data["ts_code"].astype(str)
        data["trade_date"] = data["trade_date"].astype(str)
        n = 0
        with self.Session() as s:
            for i in range(0, len(data), chunk):
                part = data.iloc[i : i + chunk]
                part = part.astype(object).where(pd.notnull(part), None)
                rows = part.to_dict(orient="records")
                stmt = sqlite_insert(DailyBar).values(rows)
                update_cols = {
                    c: getattr(stmt.excluded, c)
                    for c in keep
                    if c not in {"ts_code", "trade_date"}
                }
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ts_code", "trade_date"], set_=update_cols
                )
                s.execute(stmt)
                n += len(rows)
            s.commit()
        return n

    def replace_industry(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        now = utc_now()
        rows = []
        for r in df.itertuples(index=False):
            rows.append(
                {
                    "ts_code": str(r.ts_code),
                    "industry": None
                    if pd.isna(getattr(r, "industry", None))
                    else str(r.industry),
                    "industry_source": None
                    if "industry_source" not in df.columns
                    or pd.isna(getattr(r, "industry_source", None))
                    else str(r.industry_source),
                    "updated_at": now,
                }
            )
        with self.Session() as s:
            s.execute(delete(IndustryMap))
            s.execute(sqlite_insert(IndustryMap), rows)
            s.commit()
        return len(rows)

    def save_data_version(self, meta: dict[str, Any]) -> None:
        with self.Session() as s:
            s.execute(update(DataVersion).values(is_current=False))
            row = DataVersion(
                data_version=str(meta.get("data_version")),
                synced_at=meta.get("synced_at"),
                source=meta.get("source"),
                universe=meta.get("universe"),
                start=meta.get("start"),
                end=meta.get("end"),
                n_codes_ok=meta.get("n_codes_ok"),
                n_rows=(meta.get("quality") or {}).get("n_rows"),
                has_industry=bool(meta.get("has_industry")),
                has_circ_mv=bool(meta.get("has_circ_mv")),
                meta_json=json.dumps(meta, ensure_ascii=False),
                is_current=True,
            )
            s.merge(row)
            s.commit()

    def current_data_version(self) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.scalar(select(DataVersion).where(DataVersion.is_current.is_(True)))
            if not row:
                return None
            if row.meta_json:
                return json.loads(row.meta_json)
            return {
                "data_version": row.data_version,
                "n_codes_ok": row.n_codes_ok,
                "source": row.source,
            }

    def load_bars(self) -> pd.DataFrame:
        # Faster than ORM row materialization for full panels.
        from qfactor.db.models import get_engine

        cols = [c.name for c in DailyBar.__table__.columns if c.name != "id"]
        sql = f"SELECT {', '.join(cols)} FROM daily_bars"
        return pd.read_sql_query(sql, get_engine(self.db_url))

    def load_universe(self, universe: str) -> pd.DataFrame:
        with self.Session() as s:
            rows = s.execute(
                select(UniverseMember).where(UniverseMember.universe == universe)
            ).scalars().all()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "trade_date": r.trade_date,
                    "ts_code": r.ts_code,
                    "name": r.name,
                    "weight": r.weight,
                    "source": r.source,
                    "file_date": r.file_date,
                }
                for r in rows
            ]
        )

    def load_industry(self) -> pd.DataFrame:
        with self.Session() as s:
            rows = s.execute(select(IndustryMap)).scalars().all()
        if not rows:
            return pd.DataFrame(columns=["ts_code", "industry", "industry_source"])
        return pd.DataFrame(
            [
                {
                    "ts_code": r.ts_code,
                    "industry": r.industry,
                    "industry_source": r.industry_source,
                }
                for r in rows
            ]
        )

    def upsert_factor(self, entry: dict[str, Any], spec: dict[str, Any] | None = None) -> None:
        summary = entry.get("summary") or {}
        with self.Session() as s:
            row = FactorRow(
                name=entry["name"],
                version=str(entry.get("version") or "0.1.0"),
                status=str(entry.get("status") or "draft"),
                family=str(entry.get("family") or "price_volume"),
                category=entry.get("category"),
                source=entry.get("source"),
                mechanism=(spec or {}).get("mechanism") or entry.get("category"),
                expression=(spec or {}).get("expression"),
                hypothesis=(spec or {}).get("hypothesis"),
                entry_gate=(spec or {}).get("entry_gate"),
                path=entry.get("path"),
                expr_hash=(spec or {}).get("expr_hash"),
                rank_ic_mean=summary.get("rank_ic_mean"),
                icir=summary.get("icir"),
                coverage=summary.get("coverage"),
                max_corr=summary.get("max_corr"),
                oos_ic_mean=summary.get("oos_ic_mean"),
                cost_adjusted_ls=summary.get("cost_adjusted_ls"),
                summary_json=json.dumps(summary, ensure_ascii=False),
                spec_json=json.dumps(spec or {}, ensure_ascii=False),
                updated_at=utc_now(),
            )
            s.merge(row)
            s.commit()

    def save_factor_report(self, name: str, report: dict[str, Any]) -> None:
        with self.Session() as s:
            s.execute(
                update(FactorReport)
                .where(FactorReport.name == name, FactorReport.is_latest.is_(True))
                .values(is_latest=False)
            )
            s.add(
                FactorReport(
                    name=name,
                    gate_name=report.get("gate_name"),
                    status=(report.get("gate") or {}).get("status")
                    or (report.get("summary") or {}).get("status"),
                    created_at=utc_now(),
                    report_json=json.dumps(report, ensure_ascii=False, default=str),
                    is_latest=True,
                )
            )
            s.commit()

    def list_factors(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.Session() as s:
            q = select(FactorRow)
            if status:
                q = q.where(FactorRow.status == status)
            rows = s.execute(q.order_by(FactorRow.name)).scalars().all()
        out = []
        for r in rows:
            out.append(
                {
                    "name": r.name,
                    "version": r.version,
                    "status": r.status,
                    "family": r.family,
                    "category": r.category,
                    "source": r.source,
                    "path": r.path,
                    "summary": json.loads(r.summary_json) if r.summary_json else {},
                    "expression": r.expression,
                    "mechanism": r.mechanism,
                }
            )
        return out

    def get_latest_report(self, name: str) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.scalar(
                select(FactorReport)
                .where(FactorReport.name == name, FactorReport.is_latest.is_(True))
                .order_by(FactorReport.id.desc())
            )
            if not row:
                return None
            return json.loads(row.report_json)

    def delete_factor(self, name: str) -> None:
        with self.Session() as s:
            s.execute(delete(FactorRow).where(FactorRow.name == name))
            s.execute(delete(FactorReport).where(FactorReport.name == name))
            s.commit()

    def create_job(self, job_type: str, params: dict[str, Any] | None = None) -> int:
        now = utc_now()
        with self.Session() as s:
            job = JobRun(
                job_type=job_type,
                state="running",
                created_at=now,
                updated_at=now,
                params_json=json.dumps(params or {}, ensure_ascii=False),
            )
            s.add(job)
            s.commit()
            s.refresh(job)
            return int(job.id)

    def finish_job(
        self,
        job_id: int,
        state: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.Session() as s:
            job = s.get(JobRun, job_id)
            if not job:
                return
            job.state = state
            job.updated_at = utc_now()
            job.result_json = json.dumps(result or {}, ensure_ascii=False, default=str)
            job.error = error
            s.commit()

    def latest_job(self, job_type: str) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.scalar(
                select(JobRun)
                .where(JobRun.job_type == job_type)
                .order_by(JobRun.id.desc())
                .limit(1)
            )
            if not row:
                return None
            return {
                "id": row.id,
                "job_type": row.job_type,
                "state": row.state,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "params": json.loads(row.params_json or "{}"),
                "result": json.loads(row.result_json or "{}"),
                "error": row.error,
            }

    def save_checkpoint(self, name: str, payload: dict[str, Any]) -> None:
        with self.Session() as s:
            s.merge(
                LoopCheckpoint(
                    name=name,
                    iteration=int(payload.get("iteration") or 0),
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    updated_at=utc_now(),
                )
            )
            s.commit()

    def load_checkpoint(self, name: str) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.get(LoopCheckpoint, name)
            if not row:
                return None
            return json.loads(row.payload_json)

    def create_experiment(self, experiment_id: str, manifest: dict[str, Any]) -> None:
        with self.Session() as s:
            s.merge(
                ResearchExperiment(
                    experiment_id=experiment_id,
                    state=str(manifest.get("state") or "running"),
                    created_at=str(manifest.get("created_at") or utc_now()),
                    closed_at=manifest.get("closed_at"),
                    data_version=manifest.get("data_version"),
                    manifest_json=json.dumps(manifest, ensure_ascii=False, default=str),
                    updated_at=utc_now(),
                )
            )
            s.commit()

    def update_experiment(self, experiment_id: str, manifest: dict[str, Any]) -> None:
        self.create_experiment(experiment_id, manifest)

    def save_experiment_trial(self, experiment_id: str, event: dict[str, Any]) -> None:
        with self.Session() as s:
            s.add(
                ResearchTrial(
                    experiment_id=experiment_id,
                    trial_id=str(event.get("trial_id") or ""),
                    stage=str(event.get("stage") or "unknown"),
                    outcome=str(event.get("outcome") or "unknown"),
                    source=event.get("source"),
                    name=event.get("name"),
                    expr_hash=event.get("expr_hash"),
                    created_at=str(event.get("timestamp") or utc_now()),
                    event_json=json.dumps(event, ensure_ascii=False, default=str),
                )
            )
            s.commit()

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.get(ResearchExperiment, experiment_id)
            if not row:
                return None
            return json.loads(row.manifest_json)

    def list_experiment_trials(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.Session() as s:
            rows = s.execute(
                select(ResearchTrial)
                .where(ResearchTrial.experiment_id == experiment_id)
                .order_by(ResearchTrial.id)
            ).scalars().all()
        return [json.loads(r.event_json) for r in rows]

    def save_acceptance(self, acceptance_id: str, payload: dict[str, Any]) -> None:
        with self.Session() as s:
            s.merge(
                FactorAcceptance(
                    acceptance_id=acceptance_id,
                    name=str(payload["name"]),
                    definition_hash=str(payload["definition_hash"]),
                    state=str(payload["state"]),
                    data_version=payload.get("data_version"),
                    created_at=str(payload.get("created_at") or utc_now()),
                    report_json=json.dumps(payload, ensure_ascii=False, default=str),
                )
            )
            s.commit()

    def get_acceptance(self, acceptance_id: str) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.get(FactorAcceptance, acceptance_id)
            return json.loads(row.report_json) if row else None

    def save_release(self, release_id: str, payload: dict[str, Any]) -> None:
        with self.Session() as s:
            s.merge(
                FactorRelease(
                    release_id=release_id,
                    name=str(payload["name"]),
                    version=str(payload.get("version") or "0.1.0"),
                    state=str(payload["state"]),
                    data_version=payload.get("data_version"),
                    acceptance_id=payload.get("acceptance_id"),
                    created_at=str(payload.get("created_at") or utc_now()),
                    manifest_json=json.dumps(payload, ensure_ascii=False, default=str),
                )
            )
            s.commit()

    def list_releases(self, state: str | None = None) -> list[dict[str, Any]]:
        with self.Session() as s:
            q = select(FactorRelease)
            if state:
                q = q.where(FactorRelease.state == state)
            rows = s.execute(q.order_by(FactorRelease.created_at.desc())).scalars().all()
        return [json.loads(r.manifest_json) for r in rows]

    def log_library_op(
        self, name: str, action: str, to_status: str = "", reason: str = ""
    ) -> None:
        with self.Session() as s:
            s.add(
                LibraryOpLog(
                    ts=utc_now(),
                    name=name,
                    action=action,
                    to_status=to_status or None,
                    reason=reason or None,
                )
            )
            s.commit()

    def status(self) -> dict[str, Any]:
        with self.Session() as s:
            n_bars = s.scalar(select(func.count()).select_from(DailyBar)) or 0
            n_codes = s.scalar(select(func.count(func.distinct(DailyBar.ts_code)))) or 0
            n_factors = s.scalar(select(func.count()).select_from(FactorRow)) or 0
            n_univ = s.scalar(select(func.count()).select_from(UniverseMember)) or 0
            n_ind = s.scalar(select(func.count()).select_from(IndustryMap)) or 0
        return {
            "n_bars": int(n_bars),
            "n_codes": int(n_codes),
            "n_universe_rows": int(n_univ),
            "n_industry": int(n_ind),
            "n_factors": int(n_factors),
            "current_data_version": self.current_data_version(),
            "latest_sync_job": self.latest_job("sync"),
            "latest_loop_job": self.latest_job("loop"),
        }
