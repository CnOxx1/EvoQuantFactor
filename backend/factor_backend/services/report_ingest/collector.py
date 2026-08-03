from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from factor_backend.config import Settings, get_settings
from factor_backend.services.news_summarize import enqueue_news_summarize
from factor_backend.services.report_ingest.eastmoney import EastmoneyReportClient
from factor_backend.services.report_ingest.eastmoney_news import DEFAULT_COLUMNS, EastmoneyNewsClient
from factor_backend.services.report_ingest.jin10 import Jin10Client
from factor_backend.services.report_ingest.luobo import LuoboAuthError, LuoboClient
from factor_backend.services.report_ingest.sina_finance import SinaFinanceClient
from factor_backend.services.report_ingest.ths_news import ThsNewsClient
from factor_backend.services.report_ingest.wallstreetcn import WallstreetcnClient
from factor_backend.services.storage import Storage, get_storage

logger = logging.getLogger(__name__)

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_status: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_added": 0,
    "last_skipped": 0,
    "last_errors": [],
    "last_error": None,
    "total_runs": 0,
    "last_sources": [],
    "luobo_configured": False,
}


def get_collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_status)


def _set_status(**kwargs: Any) -> None:
    with _lock:
        _status.update(kwargs)


def _parse_news_columns(settings: Settings) -> list[tuple[int, str]]:
    raw = (settings.report_collector_news_columns or "").strip()
    if not raw:
        return list(DEFAULT_COLUMNS)
    label_map = {cid: label for cid, label in DEFAULT_COLUMNS}
    out: list[tuple[int, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            cid = int(part)
        except ValueError:
            continue
        out.append((cid, label_map.get(cid, str(cid))))
    return out or list(DEFAULT_COLUMNS)


def _collect_eastmoney_reports(storage: Storage, settings: Settings, errors: list[str]) -> tuple[int, int]:
    client = EastmoneyReportClient(request_gap_sec=settings.report_collector_request_gap_sec)
    q_types = settings.report_collector_qtype_list()
    added = 0
    skipped = 0
    items = client.iter_recent_items(
        q_types=q_types,
        page_size=settings.report_collector_page_size,
        lookback_hours=settings.report_collector_lookback_hours,
    )
    for item in items:
        info_code = str(item.get("_info_code") or "")
        external_id = f"eastmoney:{info_code}"
        try:
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            content, meta = client.fetch_report_text(item)
            title = str(item.get("title") or item.get("Title") or info_code)
            filename = f"eastmoney_{info_code}.txt"
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            saved = storage.save_report(content=content, filename=filename, title=title, meta=meta)
            added += 1
            try:
                enqueue_news_summarize(saved["report_id"])
            except Exception:  # noqa: BLE001
                logger.exception("enqueue news summarize failed %s", saved.get("report_id"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"report:{info_code}: {e}"[:240])
            logger.exception("collect report failed %s", info_code)
    return added, skipped


def _collect_eastmoney_news(storage: Storage, settings: Settings, errors: list[str]) -> tuple[int, int]:
    client = EastmoneyNewsClient(request_gap_sec=max(0.5, settings.report_collector_request_gap_sec * 0.7))
    columns = _parse_news_columns(settings)
    added = 0
    skipped = 0
    items = client.iter_recent_items(columns=columns, page_size=settings.report_collector_page_size)
    for item in items:
        news_id = str(item.get("_news_id") or item.get("code") or "")
        external_id = f"eastmoney_news:{news_id}"
        try:
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            content, meta = client.fetch_article_text(item)
            title = str(item.get("title") or news_id)
            filename = f"eastmoney_news_{news_id}.txt"
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            saved = storage.save_report(content=content, filename=filename, title=title, meta=meta)
            added += 1
            try:
                enqueue_news_summarize(saved["report_id"])
            except Exception:  # noqa: BLE001
                logger.exception("enqueue news summarize failed %s", saved.get("report_id"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"news:{news_id}: {e}"[:240])
            logger.exception("collect news failed %s", news_id)
    return added, skipped


def _collect_generic(
    *,
    storage: Storage,
    settings: Settings,
    errors: list[str],
    source_name: str,
    client: Any,
) -> tuple[int, int]:
    """通用：iter_recent_items + fetch_item_text + save + 入队摘要。"""
    added = 0
    skipped = 0
    try:
        items = client.iter_recent_items(page_size=settings.report_collector_page_size)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{source_name}:list: {e}"[:240])
        logger.exception("%s list failed", source_name)
        return 0, 0
    for item in items:
        try:
            content, meta = client.fetch_item_text(item)
            external_id = str(meta.get("external_id") or "")
            if not external_id:
                continue
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            title = str(
                meta.get("title")
                or item.get("title")
                or item.get("content_text")
                or external_id
            )
            if len(title) > 200:
                title = title[:200]
            safe = re.sub(r"[^\w\-]+", "_", external_id)[:80]
            filename = f"{source_name}_{safe}.txt"
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            saved = storage.save_report(content=content, filename=filename, title=title, meta=meta)
            added += 1
            try:
                enqueue_news_summarize(saved["report_id"])
            except Exception:  # noqa: BLE001
                logger.exception("enqueue news summarize failed %s", saved.get("report_id"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source_name}:item: {e}"[:240])
            logger.exception("%s item failed", source_name)
    return added, skipped


def _collect_luobo(storage: Storage, settings: Settings, errors: list[str]) -> tuple[int, int]:
    client = LuoboClient(
        cloud_sso_token=settings.luobo_cloud_sso_token,
        cookie=settings.luobo_cookie,
        request_gap_sec=settings.report_collector_request_gap_sec,
    )
    if not client.configured():
        msg = "luobo: 未配置 LUOBO_CLOUD_SSO_TOKEN（浏览器登录萝卜投研后复制 Cookie）"
        errors.append(msg)
        logger.warning(msg)
        return 0, 0

    added = 0
    skipped = 0
    jobs: list[tuple[str, dict[str, Any]]] = []
    try:
        if settings.luobo_collect_feeds:
            for it in client.iter_recent_feeds(page_size=settings.report_collector_page_size):
                jobs.append(("feed", it))
        if settings.luobo_collect_reports:
            for it in client.iter_recent_reports(page_size=settings.report_collector_page_size):
                jobs.append(("report", it))
    except LuoboAuthError as e:
        errors.append(str(e)[:240])
        logger.warning("%s", e)
        return 0, 0
    except Exception as e:  # noqa: BLE001
        errors.append(f"luobo:list: {e}"[:240])
        logger.exception("luobo list failed")
        return 0, 0

    for kind, item in jobs:
        if kind == "feed":
            eid_key = str(item.get("_feed_id") or item.get("id") or "")
            external_id = f"luobo:feed:{eid_key}"
            filename = f"luobo_feed_{eid_key}.txt"
            title_fallback = eid_key
            fetch = client.fetch_feed_text
        else:
            eid_key = str(item.get("_report_id") or item.get("id") or "")
            external_id = f"luobo:report:{eid_key}"
            filename = f"luobo_report_{eid_key}.txt"
            title_fallback = eid_key
            fetch = client.fetch_report_text
        try:
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            content, meta = fetch(item)
            title = str(item.get("title") or item.get("reportTitle") or title_fallback)
            if storage.find_report_by_external_id(external_id):
                skipped += 1
                continue
            saved = storage.save_report(content=content, filename=filename, title=title, meta=meta)
            added += 1
            try:
                enqueue_news_summarize(saved["report_id"])
            except Exception:  # noqa: BLE001
                logger.exception("enqueue news summarize failed %s", saved.get("report_id"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"luobo:{kind}:{eid_key}: {e}"[:240])
            logger.exception("collect luobo %s failed %s", kind, eid_key)
    return added, skipped


def run_collect_once(storage: Storage | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """执行一轮采集：多源入库 + 入队资讯摘要（不创建因子任务）。"""
    settings = settings or get_settings()
    storage = storage or get_storage()
    sources = settings.report_collector_source_list()
    started = datetime.now(timezone.utc).isoformat()
    _set_status(
        running=True,
        last_started_at=started,
        last_error=None,
        last_errors=[],
        last_sources=sources,
        luobo_configured=settings.luobo_configured(),
    )

    added = 0
    skipped = 0
    errors: list[str] = []
    try:
        if "eastmoney" in sources or "eastmoney_report" in sources:
            a, s = _collect_eastmoney_reports(storage, settings, errors)
            added += a
            skipped += s
        if "eastmoney_news" in sources:
            a, s = _collect_eastmoney_news(storage, settings, errors)
            added += a
            skipped += s
        if "wallstreetcn" in sources:
            a, s = _collect_generic(
                storage=storage,
                settings=settings,
                errors=errors,
                source_name="wallstreetcn",
                client=WallstreetcnClient(request_gap_sec=settings.report_collector_request_gap_sec * 0.6),
            )
            added += a
            skipped += s
        if "sina" in sources:
            a, s = _collect_generic(
                storage=storage,
                settings=settings,
                errors=errors,
                source_name="sina",
                client=SinaFinanceClient(request_gap_sec=settings.report_collector_request_gap_sec * 0.6),
            )
            added += a
            skipped += s
        if "ths" in sources:
            a, s = _collect_generic(
                storage=storage,
                settings=settings,
                errors=errors,
                source_name="ths",
                client=ThsNewsClient(request_gap_sec=settings.report_collector_request_gap_sec * 0.6),
            )
            added += a
            skipped += s
        if "jin10" in sources:
            a, s = _collect_generic(
                storage=storage,
                settings=settings,
                errors=errors,
                source_name="jin10",
                client=Jin10Client(request_gap_sec=settings.report_collector_request_gap_sec),
            )
            added += a
            skipped += s
        if "luobo" in sources:
            a, s = _collect_luobo(storage, settings, errors)
            added += a
            skipped += s
    except Exception as e:  # noqa: BLE001
        errors.append(str(e)[:240])
        logger.exception("collect run failed")
        _set_status(last_error=str(e)[:500])

    finished = datetime.now(timezone.utc).isoformat()
    with _lock:
        _status["running"] = False
        _status["last_finished_at"] = finished
        _status["last_started_at"] = started
        _status["last_added"] = added
        _status["last_skipped"] = skipped
        _status["last_errors"] = errors[:20]
        if errors:
            _status["last_error"] = errors[0]
        _status["total_runs"] = int(_status.get("total_runs") or 0) + 1
        _status["last_sources"] = sources
        _status["luobo_configured"] = settings.luobo_configured()
        return dict(_status)


def refetch_eastmoney_pdf(report_id: str, storage: Storage | None = None) -> dict[str, Any]:
    """对已入库的东财研报用 http PDF 重抓正文（修复 https 挑战导致的 incomplete）。"""
    storage = storage or get_storage()
    meta = storage.get_report_meta(report_id)
    extra = dict(meta.get("meta") or {})
    info_code = str(extra.get("info_code") or "").strip()
    if not info_code.startswith("AP"):
        raise ValueError("仅支持东财 AP infoCode 研报重抓 PDF")
    client = EastmoneyReportClient()
    raw = client.download_pdf_bytes(info_code)
    from factor_backend.services.text_extract import decode_upload

    content = decode_upload(f"{info_code}.pdf", raw)
    storage.update_report_content(
        report_id,
        content=content,
        meta_patch={
            "text_incomplete": False,
            "pdf_error": None,
            "pdf_bytes": len(raw),
            "pdf_url": client.pdf_urls(info_code)[0],
            "pdf_refetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    try:
        enqueue_news_summarize(report_id, mark_pending=True)
    except Exception:  # noqa: BLE001
        logger.exception("enqueue after pdf refetch failed")
    return {"report_id": report_id, "size_bytes": len(content.encode("utf-8")), "pdf_bytes": len(raw)}


def _loop() -> None:
    settings = get_settings()
    logger.info(
        "report collector started interval=%ss sources=%s qtypes=%s",
        settings.report_collector_interval_sec,
        settings.report_collector_source_list(),
        settings.report_collector_qtype_list(),
    )
    if _stop.wait(5):
        return
    while not _stop.is_set():
        try:
            run_collect_once()
        except Exception:  # noqa: BLE001
            logger.exception("report collector tick failed")
        interval = max(60, int(get_settings().report_collector_interval_sec))
        if _stop.wait(interval):
            break
    logger.info("report collector stopped")


def start_report_collector() -> None:
    global _thread
    settings = get_settings()
    _set_status(enabled=bool(settings.report_collector_enabled))
    if not settings.report_collector_enabled:
        logger.info("report collector disabled")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="report-collector", daemon=True)
    _thread.start()


def stop_report_collector() -> None:
    _stop.set()
