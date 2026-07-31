from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from factor_backend.api.deps import require_api_token
from factor_backend.models.schemas import ReportCreateText, ReportOut
from factor_backend.services.storage import get_storage
from factor_backend.services.text_extract import decode_upload

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
    dependencies=[Depends(require_api_token)],
)


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
    return ReportOut(**meta)


@router.post("/text", response_model=ReportOut)
def upload_report_text(body: ReportCreateText) -> ReportOut:
    meta = get_storage().save_report(
        content=body.content,
        filename=body.filename or "report.txt",
        title=body.title,
        meta=body.meta or {"source": "text"},
    )
    return ReportOut(**meta)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: str) -> ReportOut:
    try:
        meta = get_storage().get_report_meta(report_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"report not found: {report_id}") from e
    return ReportOut(**meta)
