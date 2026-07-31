from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from factor_backend.api.deps import require_api_token
from factor_backend.config import get_settings
from factor_backend.models.schemas import BatchCreate, BatchSummary
from factor_backend.services.storage import get_storage
from factor_backend.services.text_extract import decode_upload
from factor_backend.services.worker import enqueue_job

router = APIRouter(
    prefix="/api/v1/batches",
    tags=["batches"],
    dependencies=[Depends(require_api_token)],
)


def _shared_meta(body_like: dict) -> dict:
    settings = get_settings()
    meta = dict(body_like.get("meta") or {})
    if body_like.get("max_round") is not None:
        meta["max_round"] = body_like["max_round"]
    else:
        meta.setdefault("max_round", settings.max_round)
    if body_like.get("mcp_enabled") is not None:
        meta["mcp_enabled"] = body_like["mcp_enabled"]
    else:
        meta.setdefault("mcp_enabled", settings.mcp_enabled)
    if body_like.get("timeout_sec") is not None:
        meta["timeout_sec"] = body_like["timeout_sec"]
    return meta


def _enqueue_job(*, report_id: str, title: str | None, meta: dict, batch_id: str):
    storage = get_storage()
    job = storage.create_job(report_id=report_id, title=title, meta=meta, batch_id=batch_id)
    enqueue_job(job["job_id"])
    return job


@router.post("", response_model=BatchSummary)
def create_batch(body: BatchCreate) -> BatchSummary:
    """按 report_ids / 文本 items 批量创建任务，worker 并发执行。"""
    storage = get_storage()
    items: list[dict] = []

    for rid in body.report_ids or []:
        items.append({"report_id": rid})
    for it in body.items or []:
        items.append(it.model_dump())

    if not items:
        raise HTTPException(400, "请提供 report_ids 或 items")

    meta = _shared_meta(body.model_dump())
    batch = storage.create_batch(title=body.title, total=len(items), meta=meta)
    batch_id = batch["batch_id"]

    for it in items:
        report_id = it.get("report_id")
        title = it.get("title")
        if it.get("content") and not report_id:
            report = storage.save_report(
                content=it["content"],
                filename=it.get("filename") or "inline.txt",
                title=title,
                meta={"source": "batch_inline", "batch_id": batch_id},
            )
            report_id = report["report_id"]
            title = title or report.get("title")
        elif report_id:
            try:
                report = storage.get_report_meta(report_id)
            except FileNotFoundError as e:
                raise HTTPException(404, f"report not found: {report_id}") from e
            title = title or report.get("title")
        else:
            raise HTTPException(400, "每个 item 需要 report_id 或 content")
        _enqueue_job(report_id=report_id, title=title, meta=meta, batch_id=batch_id)

    return storage.to_batch_summary(storage.get_batch(batch_id))


@router.post("/from-upload", response_model=BatchSummary)
async def create_batch_from_upload(
    files: list[UploadFile] = File(...),
    title: str | None = Form(default=None),
    max_round: int | None = Form(default=None),
    timeout_sec: int | None = Form(default=None),
) -> BatchSummary:
    """一次上传多份研报并批量入队。"""
    if not files:
        raise HTTPException(400, "请至少上传一个文件")

    storage = get_storage()
    meta = _shared_meta({"max_round": max_round, "timeout_sec": timeout_sec, "meta": {}})
    batch = storage.create_batch(title=title or f"upload-{len(files)}", total=len(files), meta=meta)
    batch_id = batch["batch_id"]

    for f in files:
        raw = await f.read()
        if not raw:
            continue
        try:
            content = decode_upload(f.filename or "report.txt", raw)
        except ValueError as e:
            raise HTTPException(400, f"{f.filename}: {e}") from e
        report = storage.save_report(
            content=content,
            filename=f.filename or "report.txt",
            title=None,
            meta={"source": "batch_upload", "batch_id": batch_id},
        )
        _enqueue_job(
            report_id=report["report_id"],
            title=report.get("title"),
            meta=meta,
            batch_id=batch_id,
        )

    refreshed = storage.get_batch(batch_id)
    # 若有空文件被跳过，更新 total
    return storage.to_batch_summary(refreshed)


@router.get("", response_model=list[BatchSummary])
def list_batches(limit: int = 50) -> list[BatchSummary]:
    storage = get_storage()
    return [storage.to_batch_summary(b) for b in storage.list_batches(limit=limit)]


@router.get("/{batch_id}", response_model=BatchSummary)
def get_batch(batch_id: str) -> BatchSummary:
    try:
        data = get_storage().get_batch(batch_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"batch not found: {batch_id}") from e
    return get_storage().to_batch_summary(data)


@router.post("/{batch_id}/cancel", response_model=BatchSummary)
def cancel_batch(batch_id: str) -> BatchSummary:
    try:
        data = get_storage().cancel_batch(batch_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"batch not found: {batch_id}") from e
    return get_storage().to_batch_summary(data)
