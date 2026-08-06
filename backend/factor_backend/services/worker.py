from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from factor_backend.config import get_settings
from factor_backend.services import metrics
from factor_backend.services.pipeline import PipelineRunner
from factor_backend.services.storage import get_storage

logger = logging.getLogger(__name__)

_worker_threads: list[threading.Thread] = []
_stop = threading.Event()
_wake = threading.Event()


class JobCancelled(Exception):
    pass


class JobTimedOut(Exception):
    pass


def enqueue_job(job_id: str) -> None:
    """任务已写入 DB 为 queued；唤醒空闲 worker，避免空等 poll 间隔。"""
    if not job_id:
        raise ValueError("job_id required")
    try:
        job = get_storage().get_job(job_id)
    except FileNotFoundError:
        logger.error("enqueue_job: job not found: %s", job_id)
        raise
    status = job.get("status")
    logger.info("job enqueued: %s status=%s", job_id, status)
    _wake.set()


def worker_status() -> dict:
    alive = [t.name for t in _worker_threads if t.is_alive()]
    return {
        "enabled": bool(get_settings().worker_enabled),
        "configured": max(1, int(get_settings().worker_concurrency)),
        "alive": len(alive),
        "threads": alive,
        "stopping": _stop.is_set(),
    }


async def _run_with_guards(job_id: str, timeout_sec: int) -> None:
    storage = get_storage()
    runner_task = asyncio.create_task(PipelineRunner(storage=storage).run_job(job_id))

    async def poll_cancel() -> None:
        while not runner_task.done():
            if storage.is_cancel_requested(job_id):
                runner_task.cancel()
                return
            await asyncio.sleep(0.5)

    poll_task = asyncio.create_task(poll_cancel())
    try:
        # 不用 shield：超时/取消必须真正打断 LLM 请求，否则会一直卡在 running
        await asyncio.wait_for(runner_task, timeout=timeout_sec)
    except asyncio.TimeoutError as e:
        if not runner_task.done():
            runner_task.cancel()
        storage.mark_timed_out(job_id, f"exceeded timeout_sec={timeout_sec}")
        raise JobTimedOut(str(e)) from e
    except asyncio.CancelledError as e:
        if not runner_task.done():
            runner_task.cancel()
        storage.mark_cancelled(job_id, "cancelled by user")
        raise JobCancelled("cancelled") from e
    finally:
        poll_task.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, Exception):
            pass


def _run_loop(worker_id: str) -> None:
    settings = get_settings()
    poll = float(settings.worker_poll_interval)
    logger.info("job worker started: %s", worker_id)
    storage = get_storage()
    while not _stop.is_set():
        try:
            storage.reclaim_stale_jobs()
            job = storage.claim_next_job(worker_id)
            if not job:
                # 无任务时等待 poll，或被 enqueue_job 唤醒
                _wake.clear()
                _wake.wait(timeout=poll)
                continue
            job_id = job["job_id"]
            if job.get("cancel_requested"):
                storage.mark_cancelled(job_id, "cancelled before run")
                continue
            timeout_sec = int(job.get("timeout_sec") or settings.job_timeout_sec)
            metrics.incr("jobs_claimed_total")
            logger.info("claimed job %s by %s timeout=%s", job_id, worker_id, timeout_sec)
            try:
                asyncio.run(_run_with_guards(job_id, timeout_sec))
                # 成功路径由 pipeline/persist 写 succeeded；此处按最终状态计数
                try:
                    st = storage.get_job(job_id).get("status")
                except Exception:  # noqa: BLE001
                    st = None
                if st == "succeeded":
                    metrics.incr("jobs_succeeded_total")
                elif st == "failed":
                    metrics.incr("jobs_failed_total")
            except JobCancelled:
                metrics.incr("jobs_cancelled_total")
                logger.info("job cancelled: %s", job_id)
            except JobTimedOut:
                metrics.incr("jobs_timed_out_total")
                logger.warning("job timed out: %s", job_id)
            except Exception:  # noqa: BLE001
                metrics.incr("jobs_failed_total")
                logger.exception("job failed: %s", job_id)
        except Exception:  # noqa: BLE001
            logger.exception("worker loop error (%s)", worker_id)
            _stop.wait(poll)
    logger.info("job worker stopped: %s", worker_id)


def start_worker() -> None:
    global _worker_threads
    if _worker_threads and any(t.is_alive() for t in _worker_threads):
        return
    _stop.clear()
    _wake.clear()
    settings = get_settings()
    n = max(1, int(settings.worker_concurrency))
    _worker_threads = []
    for i in range(n):
        wid = f"worker_{i}_{uuid.uuid4().hex[:6]}"
        t = threading.Thread(target=_run_loop, args=(wid,), name=f"factor-job-worker-{i}", daemon=True)
        t.start()
        _worker_threads.append(t)
    logger.info("started %s worker threads", n)


def stop_worker() -> None:
    _stop.set()
    _wake.set()
