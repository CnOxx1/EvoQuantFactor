from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from factor_backend.config import Settings, get_settings
from factor_backend.services.news_summarize import enqueue_news_summarize, get_summarize_status
from factor_backend.services.report_ingest.eastmoney import EastmoneyReportClient
from factor_backend.services.report_ingest.eastmoney_news import DEFAULT_COLUMNS, EastmoneyNewsClient
from factor_backend.services.report_ingest.fingerprint import (
    attach_fingerprint,
    content_fingerprint,
    is_bad_title,
    title_from_stored_content,
)
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
    "luobo_auth_required": False,
    "source_stats": {},
    "pdf_refetched": 0,
    "titles_fixed": 0,
    "fingerprint_skipped": 0,
    "summarize": {},
}


def get_collector_status() -> dict[str, Any]:
    with _lock:
        out = dict(_status)
        out["source_stats"] = dict(_status.get("source_stats") or {})
    out["summarize"] = get_summarize_status()
    return out


def _set_status(**kwargs: Any) -> None:
    with _lock:
        _status.update(kwargs)


def _empty_stat() -> dict[str, Any]:
    return {
        "added": 0,
        "skipped": 0,
        "dup_fp": 0,
        "failed": 0,
        "elapsed_ms": 0,
        "auth_error": False,
    }


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


def _save_if_new(
    storage: Storage,
    settings: Settings,
    *,
    content: str,
    filename: str,
    title: str,
    meta: dict[str, Any],
    run_fps: set[str],
) -> str:
    """返回 added | skipped | dup_fp。"""
    external_id = str(meta.get("external_id") or "")
    if external_id and storage.find_report_by_external_id(external_id):
        return "skipped"
    meta = attach_fingerprint(meta, title, content)
    fp = str(meta.get("content_fp") or "")
    if settings.report_collector_fingerprint_dedupe and fp:
        if fp in run_fps:
            return "dup_fp"
        existing = storage.find_report_by_content_fp(fp)
        if existing:
            return "dup_fp"
        run_fps.add(fp)
    saved = storage.save_report(content=content, filename=filename, title=title, meta=meta)
    try:
        enqueue_news_summarize(saved["report_id"])
    except Exception:  # noqa: BLE001
        logger.exception("enqueue news summarize failed %s", saved.get("report_id"))
    return "added"


def _tally(result: str, counters: dict[str, int]) -> None:
    if result == "added":
        counters["added"] += 1
    elif result == "dup_fp":
        counters["dup_fp"] += 1
        counters["skipped"] += 1
    else:
        counters["skipped"] += 1


def _timed_collect(
    source: str,
    source_stats: dict[str, dict[str, Any]],
    errors: list[str],
    fn: Callable[[], dict[str, int]],
) -> None:
    """fn -> counters: added/skipped/dup_fp/failed。"""
    st = source_stats.setdefault(source, _empty_stat())
    t0 = time.perf_counter()
    try:
        counters = fn()
        for k in ("added", "skipped", "dup_fp", "failed"):
            st[k] = int(st.get(k) or 0) + int(counters.get(k) or 0)
    except LuoboAuthError as e:
        st["auth_error"] = True
        st["failed"] = int(st.get("failed") or 0) + 1
        errors.append(str(e)[:240])
        logger.warning("%s", e)
    except Exception as e:  # noqa: BLE001
        st["failed"] = int(st.get("failed") or 0) + 1
        errors.append(f"{source}: {e}"[:240])
        logger.exception("%s collect failed", source)
    finally:
        st["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)


def _collect_eastmoney_reports(
    storage: Storage,
    settings: Settings,
    errors: list[str],
    run_fps: set[str],
) -> dict[str, int]:
    client = EastmoneyReportClient(request_gap_sec=settings.report_collector_request_gap_sec)
    items = client.iter_recent_items(
        q_types=settings.report_collector_qtype_list(),
        page_size=settings.report_collector_page_size,
        lookback_hours=settings.report_collector_lookback_hours,
    )
    counters = {"added": 0, "skipped": 0, "dup_fp": 0, "failed": 0}
    for item in items:
        info_code = str(item.get("_info_code") or "")
        try:
            content, meta = client.fetch_report_text(item)
            title = str(item.get("title") or item.get("Title") or info_code)
            filename = f"eastmoney_{info_code}.txt"
            result = _save_if_new(
                storage, settings, content=content, filename=filename, title=title, meta=meta, run_fps=run_fps
            )
            _tally(result, counters)
        except Exception as e:  # noqa: BLE001
            counters["failed"] += 1
            errors.append(f"eastmoney_report:{info_code}: {e}"[:240])
            logger.exception("collect report failed %s", info_code)
    return counters


def _collect_eastmoney_news(
    storage: Storage,
    settings: Settings,
    errors: list[str],
    run_fps: set[str],
) -> dict[str, int]:
    client = EastmoneyNewsClient(request_gap_sec=max(0.5, settings.report_collector_request_gap_sec * 0.7))
    items = client.iter_recent_items(
        columns=_parse_news_columns(settings),
        page_size=settings.report_collector_page_size,
    )
    counters = {"added": 0, "skipped": 0, "dup_fp": 0, "failed": 0}
    for item in items:
        news_id = str(item.get("_news_id") or item.get("code") or "")
        try:
            content, meta = client.fetch_article_text(item)
            title = str(item.get("title") or news_id)
            filename = f"eastmoney_news_{news_id}.txt"
            result = _save_if_new(
                storage, settings, content=content, filename=filename, title=title, meta=meta, run_fps=run_fps
            )
            _tally(result, counters)
        except Exception as e:  # noqa: BLE001
            counters["failed"] += 1
            errors.append(f"eastmoney_news:{news_id}: {e}"[:240])
            logger.exception("collect news failed %s", news_id)
    return counters


def _collect_generic(
    *,
    storage: Storage,
    settings: Settings,
    errors: list[str],
    source_name: str,
    client: Any,
    run_fps: set[str],
) -> dict[str, int]:
    counters = {"added": 0, "skipped": 0, "dup_fp": 0, "failed": 0}
    try:
        items = client.iter_recent_items(page_size=settings.report_collector_page_size)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{source_name}:list: {e}"[:240])
        logger.exception("%s list failed", source_name)
        return {"added": 0, "skipped": 0, "dup_fp": 0, "failed": 1}
    for item in items:
        try:
            content, meta = client.fetch_item_text(item)
            external_id = str(meta.get("external_id") or "")
            if not external_id:
                continue
            title = str(meta.get("title") or item.get("title") or item.get("content_text") or external_id)
            if len(title) > 200:
                title = title[:200]
            safe = re.sub(r"[^\w\-]+", "_", external_id)[:80]
            filename = f"{source_name}_{safe}.txt"
            result = _save_if_new(
                storage, settings, content=content, filename=filename, title=title, meta=meta, run_fps=run_fps
            )
            _tally(result, counters)
        except Exception as e:  # noqa: BLE001
            counters["failed"] += 1
            errors.append(f"{source_name}:item: {e}"[:240])
            logger.exception("%s item failed", source_name)
    return counters


def _collect_luobo(
    storage: Storage,
    settings: Settings,
    errors: list[str],
    run_fps: set[str],
) -> dict[str, int]:
    client = LuoboClient(
        cloud_sso_token=settings.luobo_cloud_sso_token,
        cookie=settings.luobo_cookie,
        request_gap_sec=settings.report_collector_request_gap_sec,
    )
    if not client.configured():
        raise LuoboAuthError("luobo: 未配置 LUOBO_CLOUD_SSO_TOKEN（请登录萝卜投研后复制 Cookie）")

    jobs: list[tuple[str, dict[str, Any]]] = []
    if settings.luobo_collect_feeds:
        for it in client.iter_recent_feeds(page_size=settings.report_collector_page_size):
            jobs.append(("feed", it))
    if settings.luobo_collect_reports:
        for it in client.iter_recent_reports(page_size=settings.report_collector_page_size):
            jobs.append(("report", it))

    counters = {"added": 0, "skipped": 0, "dup_fp": 0, "failed": 0}
    for kind, item in jobs:
        if kind == "feed":
            eid_key = str(item.get("_feed_id") or item.get("id") or "")
            filename = f"luobo_feed_{eid_key}.txt"
            title_fallback = eid_key
            fetch = client.fetch_feed_text
        else:
            eid_key = str(item.get("_report_id") or item.get("id") or "")
            filename = f"luobo_report_{eid_key}.txt"
            title_fallback = eid_key
            fetch = client.fetch_report_text
        try:
            content, meta = fetch(item)
            title = str(meta.get("title") or item.get("title") or item.get("reportTitle") or title_fallback)
            result = _save_if_new(
                storage, settings, content=content, filename=filename, title=title, meta=meta, run_fps=run_fps
            )
            _tally(result, counters)
        except LuoboAuthError:
            raise
        except Exception as e:  # noqa: BLE001
            counters["failed"] += 1
            errors.append(f"luobo:{kind}:{eid_key}: {e}"[:240])
            logger.exception("collect luobo %s failed %s", kind, eid_key)
    return counters


def backfill_bad_titles(storage: Storage | None = None, *, limit: int = 300) -> dict[str, Any]:
    """修复坏标题（如 jin10:时间戳）。"""
    storage = storage or get_storage()
    fixed = 0
    scanned = 0
    for item in storage.iter_reports(limit=limit):
        scanned += 1
        title = item.get("title")
        external_id = item.get("external_id") or (item.get("meta") or {}).get("external_id")
        if not is_bad_title(title, str(external_id) if external_id else None):
            continue
        try:
            content = storage.get_report_content(item["report_id"])
        except FileNotFoundError:
            continue
        new_title = title_from_stored_content(content, fallback=str(external_id or item["report_id"]))
        if not new_title or new_title == title or is_bad_title(new_title, str(external_id) if external_id else None):
            continue
        storage.update_report_title(
            item["report_id"],
            title=new_title,
            meta_patch={"title": new_title, "title_backfilled_at": datetime.now(timezone.utc).isoformat()},
        )
        fixed += 1
    return {"scanned": scanned, "fixed": fixed}


def _auto_refetch_incomplete_pdfs(
    storage: Storage,
    settings: Settings,
    errors: list[str],
) -> int:
    limit = max(0, int(settings.report_collector_pdf_refetch_limit))
    if limit <= 0:
        return 0
    ok = 0
    for item in storage.list_incomplete_eastmoney(limit=limit):
        rid = item["report_id"]
        try:
            refetch_eastmoney_pdf(rid, storage=storage)
            ok += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"pdf_refetch:{rid}: {e}"[:240])
            logger.warning("auto pdf refetch failed %s: %s", rid, e)
    return ok


def run_collect_once(storage: Storage | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """执行一轮采集：多源入库 + 入队资讯摘要（不创建因子任务）。"""
    settings = settings or get_settings()
    storage = storage or get_storage()
    sources = settings.report_collector_source_list()
    started = datetime.now(timezone.utc).isoformat()
    source_stats: dict[str, dict[str, Any]] = {}
    run_fps: set[str] = set()
    _set_status(
        running=True,
        last_started_at=started,
        last_error=None,
        last_errors=[],
        last_sources=sources,
        luobo_configured=settings.luobo_configured(),
        luobo_auth_required=("luobo" in sources and not settings.luobo_configured()),
        source_stats={},
        pdf_refetched=0,
        titles_fixed=0,
        fingerprint_skipped=0,
    )

    added = skipped = 0
    errors: list[str] = []
    fp_skipped = 0
    titles_fixed = 0
    pdf_refetched = 0
    luobo_auth_required = False

    try:
        if settings.report_collector_title_backfill_on_start:
            try:
                titles_fixed = int(backfill_bad_titles(storage, limit=100).get("fixed") or 0)
            except Exception:  # noqa: BLE001
                logger.exception("title backfill failed")

        def _wrap(name: str, fn: Callable[[], dict[str, int]]) -> None:
            _timed_collect(name, source_stats, errors, fn)

        if "eastmoney" in sources or "eastmoney_report" in sources:
            _wrap(
                "eastmoney_report",
                lambda: _collect_eastmoney_reports(storage, settings, errors, run_fps),
            )
        if "eastmoney_news" in sources:
            _wrap(
                "eastmoney_news",
                lambda: _collect_eastmoney_news(storage, settings, errors, run_fps),
            )
        if "wallstreetcn" in sources:
            _wrap(
                "wallstreetcn",
                lambda: _collect_generic(
                    storage=storage,
                    settings=settings,
                    errors=errors,
                    source_name="wallstreetcn",
                    client=WallstreetcnClient(request_gap_sec=settings.report_collector_request_gap_sec * 0.6),
                    run_fps=run_fps,
                ),
            )
        if "sina" in sources:
            _wrap(
                "sina",
                lambda: _collect_generic(
                    storage=storage,
                    settings=settings,
                    errors=errors,
                    source_name="sina",
                    client=SinaFinanceClient(request_gap_sec=settings.report_collector_request_gap_sec * 0.6),
                    run_fps=run_fps,
                ),
            )
        if "ths" in sources:
            _wrap(
                "ths",
                lambda: _collect_generic(
                    storage=storage,
                    settings=settings,
                    errors=errors,
                    source_name="ths",
                    client=ThsNewsClient(request_gap_sec=settings.report_collector_request_gap_sec * 0.6),
                    run_fps=run_fps,
                ),
            )
        if "jin10" in sources:
            _wrap(
                "jin10",
                lambda: _collect_generic(
                    storage=storage,
                    settings=settings,
                    errors=errors,
                    source_name="jin10",
                    client=Jin10Client(request_gap_sec=settings.report_collector_request_gap_sec),
                    run_fps=run_fps,
                ),
            )
        if "luobo" in sources:
            _wrap("luobo", lambda: _collect_luobo(storage, settings, errors, run_fps))
            if (source_stats.get("luobo") or {}).get("auth_error"):
                luobo_auth_required = True

        pdf_refetched = _auto_refetch_incomplete_pdfs(storage, settings, errors)
    except Exception as e:  # noqa: BLE001
        errors.append(str(e)[:240])
        logger.exception("collect run failed")
        _set_status(last_error=str(e)[:500])

    total_added = sum(int((source_stats.get(k) or {}).get("added") or 0) for k in source_stats)
    total_skipped = sum(int((source_stats.get(k) or {}).get("skipped") or 0) for k in source_stats)
    fp_skipped = sum(int((source_stats.get(k) or {}).get("dup_fp") or 0) for k in source_stats)
    added, skipped = total_added, total_skipped

    finished = datetime.now(timezone.utc).isoformat()
    with _lock:
        _status["running"] = False
        _status["last_finished_at"] = finished
        _status["last_started_at"] = started
        _status["last_added"] = added
        _status["last_skipped"] = skipped
        _status["last_errors"] = errors[:30]
        if errors:
            _status["last_error"] = errors[0]
        else:
            _status["last_error"] = None
        _status["total_runs"] = int(_status.get("total_runs") or 0) + 1
        _status["last_sources"] = sources
        _status["luobo_configured"] = settings.luobo_configured()
        _status["luobo_auth_required"] = luobo_auth_required or (
            not settings.luobo_configured() and "luobo" in sources
        )
        _status["source_stats"] = source_stats
        _status["pdf_refetched"] = pdf_refetched
        _status["titles_fixed"] = titles_fixed
        _status["fingerprint_skipped"] = fp_skipped
    # 必须在释放 _lock 后再读状态（非可重入锁，持锁调用会死锁）
    return get_collector_status()


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
    title = str(meta.get("title") or info_code)
    fp = content_fingerprint(title, content)
    storage.update_report_content(
        report_id,
        content=content,
        meta_patch={
            "text_incomplete": False,
            "pdf_error": None,
            "pdf_bytes": len(raw),
            "pdf_url": client.pdf_urls(info_code)[0],
            "pdf_refetched_at": datetime.now(timezone.utc).isoformat(),
            **({"content_fp": fp} if fp else {}),
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
    if settings.report_collector_title_backfill_on_start:
        try:
            r = backfill_bad_titles(limit=200)
            logger.info("title backfill on start: %s", r)
        except Exception:  # noqa: BLE001
            logger.exception("title backfill on start failed")
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
    _set_status(
        enabled=bool(settings.report_collector_enabled),
        luobo_configured=settings.luobo_configured(),
        luobo_auth_required=("luobo" in settings.report_collector_source_list() and not settings.luobo_configured()),
    )
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
