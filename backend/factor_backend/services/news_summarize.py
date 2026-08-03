from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from factor_backend.config import get_settings
from factor_backend.llm.client import LlmClient, LlmError
from factor_backend.services.llm_config import get_llm_config
from factor_backend.services.prompt_config import news_runtime_prompt
from factor_backend.services.storage import Storage, get_storage

logger = logging.getLogger(__name__)

_queue: queue.Queue[str | None] = queue.Queue()
_stop = threading.Event()
_threads: list[threading.Thread] = []
_started = False
_start_lock = threading.Lock()

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


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

    title = str(meta.get("title") or meta.get("filename") or "")
    max_chars = max(2000, int(settings.news_summarize_max_chars))
    report_text = content or ""
    if len(report_text) > max_chars:
        report_text = report_text[:max_chars] + "\n\n…[正文已截断]"

    cfg = get_llm_config()
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
            raw = await client.chat_json(system=prompt.get("system") or "", user=user, model=cfg.model_step1)
            result = raw if isinstance(raw, dict) else {"headline": title, "summary": str(raw), "key_points": []}

        patch_summary_meta(storage, report_id, status=STATUS_DONE, summary=result)
        return {"report_id": report_id, "status": STATUS_DONE, "summary": result}
    except Exception as e:  # noqa: BLE001
        err = str(e)[:500]
        logger.exception("news summarize failed %s", report_id)
        patch_summary_meta(storage, report_id, status=STATUS_FAILED, error=err)
        return {"report_id": report_id, "status": STATUS_FAILED, "error": err}


def enqueue_news_summarize(report_id: str, *, mark_pending: bool = True) -> None:
    """入库后入队；不阻塞采集线程。"""
    if not report_id:
        return
    settings = get_settings()
    if not settings.news_summarize_enabled:
        return
    ensure_news_summarize_workers()
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
        finally:
            _queue.task_done()
    logger.info("news summarize worker-%s stopped", worker_id)


def ensure_news_summarize_workers() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _stop.clear()
        n = max(1, min(4, int(get_settings().news_summarize_workers)))
        for i in range(n):
            t = threading.Thread(target=_worker_loop, args=(i + 1,), name=f"news-summarize-{i + 1}", daemon=True)
            t.start()
            _threads.append(t)
        _started = True
        logger.info("news summarize workers started n=%s", n)


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
    _started = False
