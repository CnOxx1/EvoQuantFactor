from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any

from factor_backend.config import get_settings
from factor_backend.llm.client import LlmClient, LlmError
from factor_backend.services.llm_config import get_llm_config
from factor_backend.services.prompt_config import news_runtime_prompt
from factor_backend.services.storage import Storage, get_storage

logger = logging.getLogger(__name__)

_queue: queue.Queue[str | None] = queue.Queue()
_pending_ids: set[str] = set()
_pending_lock = threading.Lock()
_stop = threading.Event()
_threads: list[threading.Thread] = []
_started = False
_start_lock = threading.Lock()
_stats_lock = threading.Lock()
_stats: dict[str, Any] = {
    "queue_depth": 0,
    "queue_max": 200,
    "enqueued_total": 0,
    "dropped_full": 0,
    "done_total": 0,
    "failed_total": 0,
    "retry_total": 0,
    "running": 0,
    "last_error": None,
}

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _desired_worker_count(settings=None) -> int:
    settings = settings or get_settings()
    cap = max(1, int(getattr(settings, "news_summarize_workers_cap", 32) or 32))
    return max(1, min(cap, int(settings.news_summarize_workers or 1)))


def get_summarize_status() -> dict[str, Any]:
    settings = get_settings()
    with _stats_lock:
        st = dict(_stats)
    st["queue_depth"] = _queue.qsize()
    with _pending_lock:
        st["pending_unique"] = len(_pending_ids)
    st["enabled"] = bool(settings.news_summarize_enabled)
    alive = len([t for t in _threads if t.is_alive()])
    st["workers"] = alive
    st["workers_configured"] = _desired_worker_count(settings)
    st["workers_cap"] = max(1, int(getattr(settings, "news_summarize_workers_cap", 32) or 32))
    return st


def _fill_template(tpl: str, mapping: dict[str, Any]) -> str:
    out = tpl
    for k, v in mapping.items():
        if isinstance(v, (dict, list)):
            val = json.dumps(v, ensure_ascii=False, indent=2)
        else:
            val = "" if v is None else str(v)
        out = out.replace("{{" + k + "}}", val)
    return out


def _mock_summary(title: str, content: str) -> dict[str, Any]:
    snippet = (content or "").strip().replace("\n", " ")
    if len(snippet) > 180:
        snippet = snippet[:180] + "…"
    return {
        "headline": title or "资讯摘要（mock）",
        "summary": snippet or "（无正文，mock 摘要）",
        "key_points": ["mock：未调用真实 LLM"],
        "entities": [],
        "topics": ["mock"],
        "sentiment": "不明",
        "time_sensitivity": "不明",
        "implications": "",
        "quality_note": "LLM mock 模式",
    }


def patch_summary_meta(
    storage: Storage,
    report_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    patch: dict[str, Any] = {
        "news_summary_status": status,
        "news_summary_updated_at": now,
    }
    if summary is not None:
        patch["news_summary"] = summary
        patch["news_summary_at"] = now
    if error is not None:
        patch["news_summary_error"] = error
    elif status == STATUS_DONE:
        patch["news_summary_error"] = None
    storage.patch_report_meta(report_id, patch)


async def summarize_report(report_id: str, *, force: bool = False) -> dict[str, Any]:
    """对单条资讯做 LLM 摘要（非因子）。结果写入 report.meta。"""
    settings = get_settings()
    storage = get_storage()
    try:
        meta = storage.get_report_meta(report_id)
        content = storage.get_report_content(report_id)
    except FileNotFoundError:
        logger.warning("news summarize skip missing report %s", report_id)
        return {"report_id": report_id, "status": STATUS_FAILED, "error": "not found"}

    extra = meta.get("meta") or {}
    prev = str(extra.get("news_summary_status") or "")
    if not force and prev == STATUS_DONE and isinstance(extra.get("news_summary"), dict):
        return {"report_id": report_id, "status": STATUS_DONE, "summary": extra["news_summary"]}

    if not settings.news_summarize_enabled and not force:
        patch_summary_meta(storage, report_id, status=STATUS_SKIPPED, error="news_summarize_enabled=false")
        return {"report_id": report_id, "status": STATUS_SKIPPED}

    patch_summary_meta(storage, report_id, status=STATUS_RUNNING)
    with _stats_lock:
        _stats["running"] = int(_stats.get("running") or 0) + 1

    title = str(meta.get("title") or meta.get("filename") or "")
    max_chars = max(2000, int(settings.news_summarize_max_chars))
    report_text = content or ""
    if len(report_text) > max_chars:
        report_text = report_text[:max_chars] + "\n\n…[正文已截断]"

    retries = max(0, int(settings.news_summarize_max_retries))
    attempt = 0
    last_err = ""
    cfg = get_llm_config()
    try:
        while attempt <= retries:
            attempt += 1
            try:
                if not cfg.should_call_llm:
                    if cfg.use_mock or not cfg.enabled:
                        result = _mock_summary(title, report_text)
                    else:
                        raise LlmError("LLM 未就绪：请配置 api_key")
                else:
                    prompt = news_runtime_prompt()
                    user = _fill_template(
                        prompt.get("user_template") or "",
                        {
                            "title": title,
                            "source": meta.get("source") or extra.get("source") or "",
                            "q_type_label": extra.get("q_type_label") or "",
                            "org": extra.get("org") or "",
                            "publish_date": extra.get("publish_date") or "",
                            "text_incomplete": bool(extra.get("text_incomplete")),
                            "report": report_text,
                        },
                    )
                    client = LlmClient(cfg)
                    raw = await client.chat_json(
                        system=prompt.get("system") or "",
                        user=user,
                        model=cfg.model_step1,
                    )
                    result = (
                        raw
                        if isinstance(raw, dict)
                        else {"headline": title, "summary": str(raw), "key_points": []}
                    )

                patch_summary_meta(storage, report_id, status=STATUS_DONE, summary=result)
                with _stats_lock:
                    _stats["done_total"] = int(_stats.get("done_total") or 0) + 1
                return {"report_id": report_id, "status": STATUS_DONE, "summary": result}
            except Exception as e:  # noqa: BLE001
                last_err = str(e)[:500]
                if attempt <= retries:
                    with _stats_lock:
                        _stats["retry_total"] = int(_stats.get("retry_total") or 0) + 1
                    logger.warning("news summarize retry %s attempt=%s: %s", report_id, attempt, e)
                    await asyncio.sleep(min(2 * attempt, 6))
                    continue
                logger.exception("news summarize failed %s", report_id)
                patch_summary_meta(storage, report_id, status=STATUS_FAILED, error=last_err)
                with _stats_lock:
                    _stats["failed_total"] = int(_stats.get("failed_total") or 0) + 1
                    _stats["last_error"] = last_err
                return {"report_id": report_id, "status": STATUS_FAILED, "error": last_err}
    finally:
        with _stats_lock:
            _stats["running"] = max(0, int(_stats.get("running") or 0) - 1)
        with _pending_lock:
            _pending_ids.discard(report_id)


def enqueue_news_summarize(report_id: str, *, mark_pending: bool = True) -> bool:
    """入库后入队；队列满或重复则跳过。返回是否入队成功。"""
    if not report_id:
        return False
    settings = get_settings()
    if not settings.news_summarize_enabled:
        return False
    ensure_news_summarize_workers()
    qmax = max(10, int(settings.news_summarize_queue_max))
    with _stats_lock:
        _stats["queue_max"] = qmax

    with _pending_lock:
        if report_id in _pending_ids:
            return False
        if _queue.qsize() >= qmax:
            with _stats_lock:
                _stats["dropped_full"] = int(_stats.get("dropped_full") or 0) + 1
            logger.warning("news summarize queue full (%s), drop %s", qmax, report_id)
            try:
                get_storage().patch_report_meta(
                    report_id,
                    {
                        "news_summary_status": STATUS_PENDING,
                        "news_summary_error": f"queue_full>{qmax}",
                        "news_summary_updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            return False
        _pending_ids.add(report_id)

    if mark_pending:
        try:
            get_storage().patch_report_meta(
                report_id,
                {
                    "news_summary_status": STATUS_PENDING,
                    "news_summary_updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("mark pending failed %s", report_id)
    _queue.put(report_id)
    with _stats_lock:
        _stats["enqueued_total"] = int(_stats.get("enqueued_total") or 0) + 1
    return True


def _worker_loop(worker_id: int) -> None:
    logger.info("news summarize worker-%s started", worker_id)
    while not _stop.is_set():
        try:
            item = _queue.get(timeout=1.0)
        except queue.Empty:
            continue
        if item is None:
            break
        try:
            asyncio.run(summarize_report(item))
        except Exception:  # noqa: BLE001
            logger.exception("news summarize worker-%s crash on %s", worker_id, item)
            with _pending_lock:
                _pending_ids.discard(item)
        finally:
            _queue.task_done()
    logger.info("news summarize worker-%s stopped", worker_id)


def ensure_news_summarize_workers() -> None:
    """按配置启动摘要 worker；已启动时可向上扩容（缩容需重启）。"""
    global _started
    with _start_lock:
        settings = get_settings()
        desired = _desired_worker_count(settings)
        with _stats_lock:
            _stats["queue_max"] = max(10, int(settings.news_summarize_queue_max))

        alive = [t for t in _threads if t.is_alive()]
        if len(alive) != len(_threads):
            _threads[:] = alive

        if not _started:
            _stop.clear()
            for i in range(desired):
                wid = i + 1
                t = threading.Thread(
                    target=_worker_loop, args=(wid,), name=f"news-summarize-{wid}", daemon=True
                )
                t.start()
                _threads.append(t)
            _started = True
            logger.info(
                "news summarize workers started n=%s (cap=%s)",
                desired,
                getattr(settings, "news_summarize_workers_cap", 32),
            )
            return

        current = len(_threads)
        if desired <= current:
            return
        for i in range(current, desired):
            wid = i + 1
            t = threading.Thread(
                target=_worker_loop, args=(wid,), name=f"news-summarize-{wid}", daemon=True
            )
            t.start()
            _threads.append(t)
        logger.info("news summarize workers scaled %s -> %s", current, desired)


def start_news_summarize_workers() -> None:
    if get_settings().news_summarize_enabled:
        ensure_news_summarize_workers()


def stop_news_summarize_workers() -> None:
    global _started
    _stop.set()
    for _ in _threads:
        _queue.put(None)
    for t in _threads:
        t.join(timeout=3)
    _threads.clear()
    with _pending_lock:
        _pending_ids.clear()
    _started = False
