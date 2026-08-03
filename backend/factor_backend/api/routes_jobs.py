from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from factor_backend.api.deps import require_api_token
from factor_backend.config import get_settings
from factor_backend.models.schemas import (
    FactorFormula,
    JobCreate,
    JobResult,
    JobSummary,
    SeedFactorIn,
    StepDetail,
    StepSummary,
    StepType,
)
from factor_backend.services.storage import get_storage
from factor_backend.services.text_extract import decode_upload
from factor_backend.services.worker import enqueue_job

router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_api_token)],
)


def _create_and_enqueue(*, report_id: str, title: str | None, meta: dict) -> JobSummary:
    storage = get_storage()
    settings = get_settings()
    job_meta = {
        **meta,
        "max_round": meta.get("max_round") or settings.max_round,
        "mcp_enabled": meta.get("mcp_enabled", settings.mcp_enabled),
    }
    job = storage.create_job(report_id=report_id, title=title, meta=job_meta)
    enqueue_job(job["job_id"])
    return storage.to_summary(job)


def _build_evaluate_stub(factors: list[SeedFactorIn], title: str | None) -> str:
    lines = [
        title or "因子优化评估",
        "",
        "以下为待评估/优化的因子清单（来自因子库，非研报原文提取）。",
        "请基于公式可实现性、经济逻辑、稳健性与可交易性进行评审，并提出可落地的修订建议。",
        "",
    ]
    for i, f in enumerate(factors, start=1):
        lines.append(f"## 因子 {i}: {f.factor_id} {f.name_zh}")
        if f.name_en:
            lines.append(f"- 英文名: {f.name_en}")
        if f.category:
            lines.append(f"- 类别: {f.category}")
        if f.source:
            lines.append(f"- 来源: {f.source}")
        if f.inputs:
            lines.append(f"- 输入: {', '.join(f.inputs)}")
        if f.frequency:
            lines.append(f"- 频率: {f.frequency}")
        if f.signal_direction:
            lines.append(f"- 信号方向: {f.signal_direction}")
        if f.economic_logic:
            lines.append(f"- 经济逻辑: {f.economic_logic}")
        lines.append(f"- 因子公式: {f.formula_or_rule}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


@router.post("", response_model=JobSummary)
def create_job(body: JobCreate) -> JobSummary:
    storage = get_storage()
    report_id = body.report_id
    title = body.title
    mode = body.mode or "extract"
    seed_factors = [f.model_dump() for f in (body.factors or [])]

    if mode == "evaluate":
        if not seed_factors:
            raise HTTPException(400, "evaluate 模式需要提供非空 factors")
        title = title or f"优化因子 · {len(seed_factors)} 个"
        content = body.content or _build_evaluate_stub(body.factors or [], title)
        if not report_id:
            report = storage.save_report(
                content=content,
                filename="evaluate_factors.txt",
                title=title,
                meta={"source": "factor_library_evaluate", "mode": "evaluate", **(body.meta or {})},
            )
            report_id = report["report_id"]
        elif body.content:
            # rare: evaluate with explicit content + existing report_id ignored for content
            pass
    elif body.content and not report_id:
        report = storage.save_report(
            content=body.content,
            filename="inline.txt",
            title=title,
            meta={"source": "job_inline", **(body.meta or {})},
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
        raise HTTPException(400, "需要提供 report_id 或 content")

    meta = dict(body.meta or {})
    if body.max_round is not None:
        meta["max_round"] = body.max_round
    if body.mcp_enabled is not None:
        meta["mcp_enabled"] = body.mcp_enabled
    if body.timeout_sec is not None:
        meta["timeout_sec"] = body.timeout_sec
    if mode == "evaluate":
        meta["mode"] = "evaluate"
        meta["seed_factors"] = seed_factors

    return _create_and_enqueue(report_id=report_id, title=title, meta=meta)


@router.post("/from-upload", response_model=JobSummary)
async def create_job_from_upload(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    max_round: int | None = Form(default=None),
) -> JobSummary:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    try:
        content = decode_upload(file.filename or "report.txt", raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    storage = get_storage()
    report = storage.save_report(
        content=content,
        filename=file.filename or "report.txt",
        title=title,
        meta={"source": "job_from_upload"},
    )
    meta = {}
    if max_round is not None:
        meta["max_round"] = max_round
    return _create_and_enqueue(
        report_id=report["report_id"],
        title=title or report.get("title"),
        meta=meta,
    )


@router.get("", response_model=list[JobSummary])
def list_jobs(limit: int = 50) -> list[JobSummary]:
    storage = get_storage()
    return [storage.to_summary(j) for j in storage.list_jobs(limit=limit)]


@router.get("/{job_id}", response_model=JobSummary)
def get_job(job_id: str) -> JobSummary:
    try:
        job = get_storage().get_job(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"job not found: {job_id}") from e
    return get_storage().to_summary(job)


@router.post("/{job_id}/cancel", response_model=JobSummary)
def cancel_job(job_id: str) -> JobSummary:
    """取消任务：queued 立即取消；running 置 cancel_requested，worker 会中断。"""
    try:
        job = get_storage().request_cancel(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"job not found: {job_id}") from e
    return get_storage().to_summary(job)


@router.post("/{job_id}/rerun", response_model=JobSummary)
def rerun_job(job_id: str) -> JobSummary:
    """再次分析：复用原任务的 report_id 与关键 meta，创建新任务。"""
    storage = get_storage()
    try:
        job = storage.get_job(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"job not found: {job_id}") from e

    report_id = job.get("report_id")
    if not report_id:
        raise HTTPException(400, "原任务无关联研报/评估上下文，无法再次分析")
    try:
        storage.get_report_meta(report_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"关联研报不存在: {report_id}") from e

    old_meta = dict(job.get("meta") or {})
    meta: dict = {"rerun_from": job_id}
    for key in (
        "max_round",
        "mcp_enabled",
        "timeout_sec",
        "mode",
        "seed_factors",
        "market",
        "symbols_hint",
        "date_range_hint",
        "failure_retries",
    ):
        if key in old_meta and old_meta[key] is not None:
            meta[key] = old_meta[key]

    base_title = (job.get("title") or "").strip() or job_id
    title = base_title if base_title.startswith("再次分析") else f"再次分析 · {base_title}"
    return _create_and_enqueue(report_id=report_id, title=title, meta=meta)


@router.get("/{job_id}/factors", response_model=list[FactorFormula])
def get_factors(
    job_id: str,
    include_candidates: bool = Query(default=True, description="是否包含未过线候选因子"),
) -> list[FactorFormula]:
    storage = get_storage()
    try:
        storage.get_job(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"job not found: {job_id}") from e
    result = storage.get_result(job_id)
    if not result:
        raise HTTPException(409, "任务尚未完成或无结果")
    out: list[FactorFormula] = [FactorFormula(**f) for f in result.get("factors", [])]
    if include_candidates:
        for f in result.get("candidates") or []:
            if isinstance(f, dict):
                out.append(FactorFormula(**{**f, "status": f.get("status") or "CANDIDATE"}))
    return out



def _step_view(s: dict) -> dict:
    payload = s.get("payload") or {}
    factor_ids: list[str] = []
    role_name = None
    if s.get("step_type") == "step2_review":
        reviews = payload.get("reviews") or {}
        if isinstance(reviews, dict):
            factor_ids = list(reviews.keys())
            for item in reviews.values():
                if isinstance(item, dict) and item.get("role_name"):
                    role_name = str(item["role_name"])
                    break
    elif s.get("step_type") == "step1_extract":
        factors = payload.get("factors") or []
        changed = payload.get("changed_ids") or []
        if isinstance(changed, list) and changed:
            factor_ids = [str(x) for x in changed]
        elif isinstance(factors, list):
            factor_ids = [str(f.get("factor_id")) for f in factors if isinstance(f, dict) and f.get("factor_id")]
    elif s.get("step_type") == "step2_merge":
        factor_ids = [str(x) for x in (payload.get("factor_ids") or [])]
    elif s.get("step_type") == "step3_gate":
        rows = payload.get("rows") or []
        if isinstance(rows, list):
            factor_ids = [str(r.get("factor_id")) for r in rows if isinstance(r, dict) and r.get("factor_id")]
    elif s.get("step_type") == "revise_loop":
        items = payload.get("items") or []
        if isinstance(items, list):
            factor_ids = [str(i.get("factor_id")) for i in items if isinstance(i, dict) and i.get("factor_id")]
    elif s.get("step_type") == "persist":
        saved = payload.get("saved") or []
        dropped = payload.get("dropped") or []
        ids: list[str] = []
        if isinstance(saved, list):
            ids.extend(str(x.get("factor_id")) for x in saved if isinstance(x, dict) and x.get("factor_id"))
        if isinstance(dropped, list):
            ids.extend(str(x.get("factor_id")) for x in dropped if isinstance(x, dict) and x.get("factor_id"))
        if not ids:
            ids = [str(x) for x in (payload.get("saved_ids") or [])] + [
                str(x) for x in (payload.get("dropped_ids") or [])
            ]
        factor_ids = ids
    return {
        "step_id": s["step_id"],
        "seq": s["seq"],
        "step_type": StepType(s["step_type"]),
        "title": s["title"],
        "round": s.get("round", 0),
        "role_code": s.get("role_code"),
        "status": s.get("status", "ok"),
        "created_at": s["created_at"],
        "summary": s.get("summary", ""),
        "factor_ids": factor_ids,
        "role_name": role_name,
        "factor_count": len(factor_ids),
        "payload": payload,
    }


@router.get("/{job_id}/steps", response_model=list[StepDetail])
def get_steps(job_id: str) -> list[StepDetail]:
    storage = get_storage()
    try:
        storage.get_job(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"job not found: {job_id}") from e
    steps = storage.list_steps(job_id)
    return [StepDetail(**_step_view(s)) for s in steps]


@router.get("/{job_id}/steps/{step_id}", response_model=StepDetail)
def get_step_detail(job_id: str, step_id: str) -> StepDetail:
    storage = get_storage()
    try:
        storage.get_job(job_id)
        s = storage.get_step(job_id, step_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return StepDetail(**_step_view(s))


@router.get("/{job_id}/result", response_model=JobResult)
def get_result(job_id: str) -> JobResult:
    storage = get_storage()
    try:
        job = storage.get_job(job_id)
    except FileNotFoundError as e:
        raise HTTPException(404, f"job not found: {job_id}") from e
    result = storage.get_result(job_id)
    steps = [
        StepSummary(
            step_id=s["step_id"],
            seq=s["seq"],
            step_type=StepType(s["step_type"]),
            title=s["title"],
            round=s.get("round", 0),
            role_code=s.get("role_code"),
            status=s.get("status", "ok"),
            created_at=s["created_at"],
            summary=s.get("summary", ""),
        )
        for s in storage.list_steps(job_id)
    ]
    if not result:
        return JobResult(
            job_id=job_id,
            status=job["status"],
            factors=[],
            dropped=[],
            steps=steps,
            rounds_used=job.get("rounds_used", 0),
            report_id=job.get("report_id"),
        )
    return JobResult(
        job_id=job_id,
        status=result.get("status", job["status"]),
        factors=[FactorFormula(**f) for f in result.get("factors", [])],
        dropped=result.get("dropped", []),
        steps=steps,
        rounds_used=result.get("rounds_used", 0),
        report_id=result.get("report_id"),
    )
