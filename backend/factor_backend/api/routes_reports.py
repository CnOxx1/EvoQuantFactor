from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from factor_backend.api.deps import require_api_token
from factor_backend.models.schemas import ReportContentOut, ReportCreateText, ReportListOut, ReportOut
from factor_backend.services.news_summarize import (
    enqueue_news_summarize,
    get_summarize_status,
    summarize_report,
)
from factor_backend.services.report_ingest.collector import (
    backfill_bad_titles,
    get_collector_status,
    refetch_eastmoney_pdf,
    run_collect_once,
)
from factor_backend.services.storage import get_storage
from factor_backend.services.text_extract import decode_upload

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=ReportListOut)
def list_reports(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    source: str | None = Query(default=None),
    suitability: str | None = Query(
        default=None,
        description="factor=适合因子粗筛；news_only=宏观/晨报/正文不完整",
    ),
) -> ReportListOut:
    data = get_storage().list_reports(
        limit=limit, offset=offset, q=q, source=source, suitability=suitability
    )
    items = [ReportOut(**x) for x in data["items"]]
    return ReportListOut(total=data["total"], offset=data["offset"], limit=data["limit"], items=items)


@router.get("/collect/status")
def collect_status() -> dict:
    return get_collector_status()


@router.post("/collect/run")
def collect_run() -> dict:
    """手动触发一轮采集（只入库，不自动因子分析；入库后会入队资讯摘要）。"""
    return run_collect_once()


@router.post("/titles/backfill")
def titles_backfill(limit: int = Query(default=300, ge=1, le=2000)) -> dict:
    """修复坏标题（如 jin10:时间戳）。"""
    return backfill_bad_titles(limit=limit)


@router.get("/summarize/status")
def summarize_status() -> dict:
    """摘要队列背压与累计统计。"""
    return get_summarize_status()


@router.post("", response_model=ReportOut)
async def upload_report_file(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
) -> ReportOut:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    try:
        content = decode_upload(file.filename or "report.txt", raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    meta = get_storage().save_report(
        content=content,
        filename=file.filename or "report.txt",
        title=title,
        meta={"source": "upload"},
    )
    enqueue_news_summarize(meta["report_id"])
    return ReportOut(**meta)


@router.post("/text", response_model=ReportOut)
def upload_report_text(body: ReportCreateText) -> ReportOut:
    meta = get_storage().save_report(
        content=body.content,
        filename=body.filename or "report.txt",
        title=body.title,
        meta=body.meta or {"source": "text"},
    )
    enqueue_news_summarize(meta["report_id"])
    return ReportOut(**meta)


@router.post("/summarize/backfill")
def summarize_backfill(
    limit: int = Query(default=50, ge=1, le=200),
    only_missing: bool = Query(default=True),
) -> dict:
    """将已有资讯入队摘要（默认只处理未完成/失败的）。"""
    storage = get_storage()
    data = storage.list_reports(limit=limit, offset=0)
    queued = 0
    skipped = 0
    for item in data["items"]:
        rid = item["report_id"]
        meta = item.get("meta") or {}
        st = str(meta.get("news_summary_status") or "")
        if only_missing and st == "done" and isinstance(meta.get("news_summary"), dict):
            skipped += 1
            continue
        enqueue_news_summarize(rid, mark_pending=True)
        queued += 1
    return {"queued": queued, "skipped": skipped, "scanned": len(data["items"])}


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: str) -> ReportOut:
    try:
        meta = get_storage().get_report_meta(report_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"report not found: {report_id}") from e
    return ReportOut(**meta)


@router.get("/{report_id}/content", response_model=ReportContentOut)
def get_report_content(report_id: str) -> ReportContentOut:
    """查看入库原文与资讯摘要。"""
    storage = get_storage()
    try:
        meta = storage.get_report_meta(report_id)
        content = storage.get_report_content(report_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"report not found: {report_id}") from e
    extra = meta.get("meta") or {}
    summary = extra.get("news_summary") if isinstance(extra.get("news_summary"), dict) else None
    return ReportContentOut(
        report_id=meta["report_id"],
        title=meta.get("title"),
        filename=meta["filename"],
        content=content or "",
        meta=extra,
        text_incomplete=bool(extra.get("text_incomplete")),
        pdf_url=extra.get("pdf_url") if isinstance(extra.get("pdf_url"), str) else None,
        news_summary_status=str(extra.get("news_summary_status") or "") or None,
        news_summary=summary,
        news_summary_error=extra.get("news_summary_error") if isinstance(extra.get("news_summary_error"), str) else None,
    )


@router.post("/{report_id}/refetch-pdf")
def refetch_pdf(report_id: str) -> dict:
    """东财研报重抓 PDF（优先 http，修复 https JS 挑战导致的正文缺失）。"""
    try:
        return refetch_eastmoney_pdf(report_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"report not found: {report_id}") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"pdf refetch failed: {e}") from e


@router.post("/{report_id}/summarize")
async def summarize_report_api(report_id: str, force: bool = Query(default=True)) -> dict:
    """手动触发 / 重跑资讯摘要（非因子流水线）。"""
    storage = get_storage()
    try:
        storage.get_report_meta(report_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"report not found: {report_id}") from e
    return await summarize_report(report_id, force=force)
