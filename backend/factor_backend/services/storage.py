from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select

from factor_backend.db.models import BatchRow, JobResultRow, JobRow, ReportRow, StepRow, get_session_factory, utcnow
from factor_backend.models.schemas import BatchStatusCounts, BatchSummary, JobProgress, JobStatus, JobSummary


class Storage:
    """生产存储：SQLAlchemy（默认 SQLite，可换 Postgres）。"""

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def save_report(
        self,
        *,
        content: str,
        filename: str,
        title: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report_id = self.new_id("rpt")
        meta = dict(meta or {})
        external_id = meta.get("external_id")
        source = meta.get("source")
        Session = get_session_factory()
        with Session() as db:
            if external_id:
                existing = db.scalar(select(ReportRow).where(ReportRow.external_id == str(external_id)).limit(1))
                if existing:
                    return self._report_dict(existing)
            row = ReportRow(
                report_id=report_id,
                title=title or filename.rsplit(".", 1)[0],
                filename=filename,
                content=content,
                size_bytes=len(content.encode("utf-8")),
                meta_json=json.dumps(meta, ensure_ascii=False),
                external_id=str(external_id) if external_id else None,
                source=str(source) if source else None,
            )
            db.add(row)
            db.commit()
            return self._report_dict(row)

    def find_report_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        Session = get_session_factory()
        with Session() as db:
            row = db.scalar(select(ReportRow).where(ReportRow.external_id == external_id).limit(1))
            return self._report_dict(row) if row else None

    def list_reports(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        source: str | None = None,
        suitability: str | None = None,
    ) -> dict[str, Any]:
        from sqlalchemy import func, or_

        Session = get_session_factory()
        with Session() as db:
            filters = []
            if source:
                filters.append(ReportRow.source == source)
            if q and q.strip():
                like = f"%{q.strip()}%"
                filters.append(
                    or_(
                        ReportRow.title.like(like),
                        ReportRow.filename.like(like),
                        ReportRow.external_id.like(like),
                        ReportRow.meta_json.like(like),
                    )
                )
            # 因子适配粗筛：宏观(q_type=3)/晨报(4)/正文不完整 → news_only；其余 factor
            news_only = or_(
                ReportRow.meta_json.contains('"text_incomplete": true'),
                ReportRow.meta_json.contains('"q_type": 3'),
                ReportRow.meta_json.contains('"q_type": 4'),
                ReportRow.meta_json.contains('"q_type_label": "宏观"'),
                ReportRow.meta_json.contains('"q_type_label": "晨报"'),
            )
            suit = (suitability or "").strip().lower()
            if suit in ("factor", "factor_suitable", "suitable"):
                filters.append(~news_only)
            elif suit in ("news", "news_only", "skip_factor"):
                filters.append(news_only)
            count_q = select(func.count()).select_from(ReportRow)
            list_q = select(ReportRow)
            for f in filters:
                count_q = count_q.where(f)
                list_q = list_q.where(f)
            total = int(db.scalar(count_q) or 0)
            rows = db.scalars(
                list_q.order_by(ReportRow.created_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))
            ).all()
            report_ids = [row.report_id for row in rows]
            job_counts: dict[str, int] = {}
            if report_ids:
                count_rows = db.execute(
                    select(JobRow.report_id, func.count())
                    .where(JobRow.report_id.in_(report_ids))
                    .group_by(JobRow.report_id)
                ).all()
                job_counts = {str(rid): int(n or 0) for rid, n in count_rows if rid}
            items = []
            for row in rows:
                d = self._report_dict(row)
                d["job_count"] = job_counts.get(row.report_id, 0)
                items.append(d)
            return {"total": total, "offset": offset, "limit": limit, "items": items}

    def get_report_meta(self, report_id: str) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(ReportRow, report_id)
            if not row:
                raise FileNotFoundError(report_id)
            return self._report_dict(row)

    def get_report_content(self, report_id: str) -> str:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(ReportRow, report_id)
            if not row:
                raise FileNotFoundError(report_id)
            return row.content

    def patch_report_meta(self, report_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """合并写入 report.meta_json（浅合并顶层键）。"""
        Session = get_session_factory()
        with Session() as db:
            row = db.get(ReportRow, report_id)
            if not row:
                raise FileNotFoundError(report_id)
            meta = json.loads(row.meta_json or "{}")
            meta.update(patch or {})
            row.meta_json = json.dumps(meta, ensure_ascii=False)
            db.commit()
            return self._report_dict(row)

    def update_report_content(
        self,
        report_id: str,
        *,
        content: str,
        meta_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(ReportRow, report_id)
            if not row:
                raise FileNotFoundError(report_id)
            row.content = content or ""
            row.size_bytes = len((content or "").encode("utf-8"))
            if meta_patch:
                meta = json.loads(row.meta_json or "{}")
                meta.update(meta_patch)
                row.meta_json = json.dumps(meta, ensure_ascii=False)
            db.commit()
            return self._report_dict(row)

    def update_report_title(
        self,
        report_id: str,
        *,
        title: str,
        meta_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(ReportRow, report_id)
            if not row:
                raise FileNotFoundError(report_id)
            row.title = (title or "")[:512] or row.title
            if meta_patch:
                meta = json.loads(row.meta_json or "{}")
                meta.update(meta_patch)
                row.meta_json = json.dumps(meta, ensure_ascii=False)
            db.commit()
            return self._report_dict(row)

    def find_report_by_content_fp(self, content_fp: str) -> dict[str, Any] | None:
        fp = (content_fp or "").strip()
        if not fp:
            return None
        Session = get_session_factory()
        with Session() as db:
            # meta_json 内 "content_fp": "xxxx"
            needle = f'"content_fp": "{fp}"'
            row = db.scalar(select(ReportRow).where(ReportRow.meta_json.contains(needle)).limit(1))
            return self._report_dict(row) if row else None

    def iter_reports(self, *, limit: int = 200, source: str | None = None) -> list[dict[str, Any]]:
        Session = get_session_factory()
        with Session() as db:
            stmt = select(ReportRow).order_by(ReportRow.created_at.desc()).limit(limit)
            if source:
                stmt = stmt.where(ReportRow.source == source)
            rows = db.scalars(stmt).all()
            return [self._report_dict(r) for r in rows]

    def list_incomplete_eastmoney(self, *, limit: int = 20) -> list[dict[str, Any]]:
        Session = get_session_factory()
        with Session() as db:
            stmt = (
                select(ReportRow)
                .where(ReportRow.source == "eastmoney")
                .where(ReportRow.meta_json.contains('"text_incomplete": true'))
                .order_by(ReportRow.created_at.desc())
                .limit(limit)
            )
            rows = db.scalars(stmt).all()
            return [self._report_dict(r) for r in rows]

    def _report_dict(self, row: ReportRow) -> dict[str, Any]:
        meta = json.loads(row.meta_json or "{}")
        return {
            "report_id": row.report_id,
            "title": row.title,
            "filename": row.filename,
            "size_bytes": row.size_bytes,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "meta": meta,
            "external_id": getattr(row, "external_id", None) or meta.get("external_id"),
            "source": getattr(row, "source", None) or meta.get("source"),
        }

    def create_job(
        self,
        *,
        report_id: str | None,
        title: str | None,
        meta: dict[str, Any],
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        from factor_backend.config import get_settings

        job_id = self.new_id("job")
        now = utcnow()
        progress = JobProgress().model_dump()
        timeout_sec = int((meta or {}).get("timeout_sec") or get_settings().job_timeout_sec)
        Session = get_session_factory()
        with Session() as db:
            row = JobRow(
                job_id=job_id,
                report_id=report_id,
                batch_id=batch_id,
                title=title,
                status=JobStatus.queued.value,
                progress_json=json.dumps(progress, ensure_ascii=False),
                meta_json=json.dumps(meta or {}, ensure_ascii=False),
                timeout_sec=timeout_sec,
                cancel_requested=False,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            return self._job_dict(row)

    def get_job(self, job_id: str) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(JobRow, job_id)
            if not row:
                raise FileNotFoundError(job_id)
            return self._job_dict(row)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(JobRow, job_id)
            if not row:
                raise FileNotFoundError(job_id)
            if "progress" in fields:
                row.progress_json = json.dumps(fields.pop("progress"), ensure_ascii=False)
            if "meta" in fields:
                row.meta_json = json.dumps(fields.pop("meta"), ensure_ascii=False)
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = utcnow()
            db.commit()
            db.refresh(row)
            return self._job_dict(row)

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        """多机安全领取：先选候选，再用 status=queued 条件更新（CAS）。"""
        Session = get_session_factory()
        lock_token = uuid.uuid4().hex
        with Session() as db:
            self._reclaim_stale_jobs_in_session(db)
            candidates = db.scalars(
                select(JobRow)
                .where(JobRow.status == JobStatus.queued.value)
                .order_by(JobRow.created_at.asc())
                .limit(5)
            ).all()
            for cand in candidates:
                now = utcnow()
                # CAS：只有仍为 queued 才能抢走
                from sqlalchemy import update

                result = db.execute(
                    update(JobRow)
                    .where(JobRow.job_id == cand.job_id, JobRow.status == JobStatus.queued.value)
                    .values(
                        status=JobStatus.running.value,
                        worker_id=worker_id,
                        lock_token=lock_token,
                        started_at=now,
                        updated_at=now,
                        progress_json=json.dumps(
                            JobProgress(phase="claimed", round=0, message="已被 worker 领取", percent=1).model_dump(),
                            ensure_ascii=False,
                        ),
                    )
                )
                db.commit()
                if result.rowcount == 1:
                    row = db.get(JobRow, cand.job_id)
                    return self._job_dict(row) if row else None
            return None

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(JobRow, job_id)
            if not row:
                raise FileNotFoundError(job_id)
            if row.status in (
                JobStatus.succeeded.value,
                JobStatus.failed.value,
                JobStatus.cancelled.value,
                JobStatus.timed_out.value,
            ):
                return self._job_dict(row)
            row.cancel_requested = True
            if row.status == JobStatus.queued.value:
                row.status = JobStatus.cancelled.value
                row.error = "cancelled before start"
            row.updated_at = utcnow()
            db.commit()
            db.refresh(row)
            return self._job_dict(row)

    def is_cancel_requested(self, job_id: str) -> bool:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(JobRow, job_id)
            return bool(row and row.cancel_requested)

    def mark_cancelled(self, job_id: str, reason: str = "cancelled") -> None:
        self.update_job(
            job_id,
            status=JobStatus.cancelled.value,
            error=reason,
            progress=JobProgress(phase="cancelled", round=0, message=reason, percent=100).model_dump(),
        )

    def mark_timed_out(self, job_id: str, reason: str = "job timeout") -> None:
        self.update_job(
            job_id,
            status=JobStatus.timed_out.value,
            error=reason,
            progress=JobProgress(phase="timeout", round=0, message=reason, percent=100).model_dump(),
        )

    def _reclaim_stale_jobs_in_session(self, db) -> int:
        """回收超时仍 running 的任务；进度长时间不更新也视为僵死。"""
        from datetime import timedelta, timezone

        now = utcnow()
        rows = db.scalars(select(JobRow).where(JobRow.status == JobStatus.running.value)).all()
        n = 0
        for row in rows:
            started = row.started_at or row.updated_at
            if started is None:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            timeout = int(row.timeout_sec or 1800)
            # 进度心跳：updated_at 长时间不动，即使未到整任务 timeout 也回收
            heartbeat = row.updated_at or started
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            stall_limit = min(timeout, max(600, int(timeout * 0.4)))
            stale_by_timeout = (now - started) > timedelta(seconds=timeout)
            stale_by_stall = (now - heartbeat) > timedelta(seconds=stall_limit)
            if stale_by_timeout or stale_by_stall:
                reason = (
                    f"stale running > {timeout}s (multi-worker reclaim)"
                    if stale_by_timeout
                    else f"progress stalled > {stall_limit}s (no updated_at heartbeat)"
                )
                row.status = JobStatus.timed_out.value
                row.error = reason
                row.updated_at = now
                row.progress_json = json.dumps(
                    JobProgress(phase="timeout", round=0, message=reason, percent=100).model_dump(),
                    ensure_ascii=False,
                )
                n += 1
        if n:
            db.commit()
        return n

    def reclaim_stale_jobs(self) -> int:
        Session = get_session_factory()
        with Session() as db:
            return self._reclaim_stale_jobs_in_session(db)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        Session = get_session_factory()
        with Session() as db:
            rows = db.scalars(select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)).all()
            return [self._job_dict(r) for r in rows]

    def _job_dict(self, row: JobRow) -> dict[str, Any]:
        return {
            "job_id": row.job_id,
            "report_id": row.report_id,
            "batch_id": getattr(row, "batch_id", None),
            "title": row.title,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            "progress": json.loads(row.progress_json or "{}"),
            "error": row.error,
            "rounds_used": row.rounds_used,
            "saved_count": row.saved_count,
            "dropped_count": row.dropped_count,
            "meta": json.loads(row.meta_json or "{}"),
            "step_seq": row.step_seq,
            "worker_id": row.worker_id,
            "cancel_requested": bool(getattr(row, "cancel_requested", False)),
            "started_at": row.started_at.isoformat() if getattr(row, "started_at", None) else None,
            "timeout_sec": getattr(row, "timeout_sec", 1800),
            "lock_token": getattr(row, "lock_token", None),
        }

    def next_step_seq(self, job_id: str) -> int:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(JobRow, job_id)
            if not row:
                raise FileNotFoundError(job_id)
            row.step_seq = int(row.step_seq or 0) + 1
            row.updated_at = utcnow()
            db.commit()
            return row.step_seq

    def append_step(self, job_id: str, step: dict[str, Any]) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            db.add(
                StepRow(
                    job_id=job_id,
                    step_id=step["step_id"],
                    seq=step["seq"],
                    step_type=step["step_type"],
                    title=step.get("title", ""),
                    round=step.get("round", 0),
                    role_code=step.get("role_code"),
                    status=step.get("status", "ok"),
                    summary=step.get("summary", ""),
                    payload_json=json.dumps(step.get("payload") or {}, ensure_ascii=False),
                )
            )
            db.commit()
        return step

    def list_steps(self, job_id: str) -> list[dict[str, Any]]:
        Session = get_session_factory()
        with Session() as db:
            rows = db.scalars(
                select(StepRow).where(StepRow.job_id == job_id).order_by(StepRow.seq.asc())
            ).all()
            return [self._step_dict(r) for r in rows]

    def get_step(self, job_id: str, step_id: str) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.scalars(
                select(StepRow).where(StepRow.job_id == job_id, StepRow.step_id == step_id)
            ).first()
            if not row:
                raise FileNotFoundError(step_id)
            return self._step_dict(row)

    def _step_dict(self, row: StepRow) -> dict[str, Any]:
        return {
            "step_id": row.step_id,
            "seq": row.seq,
            "step_type": row.step_type,
            "title": row.title,
            "round": row.round,
            "role_code": row.role_code,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "summary": row.summary,
            "payload": json.loads(row.payload_json or "{}"),
        }

    def save_result(self, job_id: str, result: dict[str, Any]) -> None:
        Session = get_session_factory()
        with Session() as db:
            existing = db.get(JobResultRow, job_id)
            payload = json.dumps(result, ensure_ascii=False)
            if existing:
                existing.result_json = payload
            else:
                db.add(JobResultRow(job_id=job_id, result_json=payload))
            db.commit()

    def get_result(self, job_id: str) -> dict[str, Any] | None:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(JobResultRow, job_id)
            if not row:
                return None
            return json.loads(row.result_json or "{}")

    def to_summary(self, job: dict[str, Any]) -> JobSummary:
        return JobSummary(
            job_id=job["job_id"],
            report_id=job.get("report_id"),
            batch_id=job.get("batch_id"),
            status=JobStatus(job["status"]),
            created_at=job["created_at"],
            updated_at=job["updated_at"],
            progress=JobProgress(**(job.get("progress") or {})),
            error=job.get("error"),
            rounds_used=job.get("rounds_used", 0),
            saved_count=job.get("saved_count", 0),
            dropped_count=job.get("dropped_count", 0),
            title=job.get("title"),
        )

    # ----- batches -----
    def create_batch(self, *, title: str | None, total: int, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        batch_id = self.new_id("bat")
        now = utcnow()
        Session = get_session_factory()
        with Session() as db:
            row = BatchRow(
                batch_id=batch_id,
                title=title or f"batch-{total}",
                status="queued",
                total=total,
                meta_json=json.dumps(meta or {}, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            return self._batch_dict(row, jobs=[])

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(BatchRow, batch_id)
            if not row:
                raise FileNotFoundError(batch_id)
            jobs = db.scalars(
                select(JobRow).where(JobRow.batch_id == batch_id).order_by(JobRow.created_at.asc())
            ).all()
            return self._batch_dict(row, jobs=[self._job_dict(j) for j in jobs])

    def list_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        Session = get_session_factory()
        with Session() as db:
            rows = db.scalars(select(BatchRow).order_by(BatchRow.created_at.desc()).limit(limit)).all()
            out = []
            for row in rows:
                jobs = db.scalars(select(JobRow).where(JobRow.batch_id == row.batch_id)).all()
                out.append(self._batch_dict(row, jobs=[self._job_dict(j) for j in jobs]))
            return out

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        Session = get_session_factory()
        with Session() as db:
            row = db.get(BatchRow, batch_id)
            if not row:
                raise FileNotFoundError(batch_id)
            jobs = db.scalars(select(JobRow).where(JobRow.batch_id == batch_id)).all()
            job_ids = [j.job_id for j in jobs]
        for jid in job_ids:
            self.request_cancel(jid)
        return self.get_batch(batch_id)

    def _batch_dict(self, row: BatchRow, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        counts = BatchStatusCounts()
        for j in jobs:
            st = j.get("status")
            if hasattr(counts, st):
                setattr(counts, st, getattr(counts, st) + 1)
        done = counts.succeeded + counts.failed + counts.cancelled + counts.timed_out
        total = max(row.total or len(jobs), 1)
        percent = int(round(100 * done / total)) if jobs else 0
        if counts.running or counts.queued:
            status = "running" if counts.running else "queued"
        elif counts.failed or counts.timed_out:
            status = "completed_with_errors"
        elif counts.cancelled and counts.succeeded == 0:
            status = "cancelled"
        elif done >= len(jobs) and jobs:
            status = "succeeded"
        else:
            status = row.status
        # persist soft status
        return {
            "batch_id": row.batch_id,
            "title": row.title,
            "status": status,
            "total": row.total or len(jobs),
            "counts": counts.model_dump(),
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            "jobs": jobs,
            "percent": percent,
            "message": f"{done}/{len(jobs)} finished" if jobs else "empty",
        }

    def to_batch_summary(self, data: dict[str, Any]) -> BatchSummary:
        return BatchSummary(
            batch_id=data["batch_id"],
            title=data.get("title"),
            status=data["status"],
            total=data.get("total", 0),
            counts=BatchStatusCounts(**(data.get("counts") or {})),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            jobs=[self.to_summary(j) for j in data.get("jobs") or []],
            percent=data.get("percent", 0),
            message=data.get("message", ""),
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage


def reset_storage_singleton() -> None:
    global _storage
    _storage = None
