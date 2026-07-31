from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class StepType(str, Enum):
    ingest = "ingest"
    step1_extract = "step1_extract"
    step2_review = "step2_review"
    step2_merge = "step2_merge"
    step3_gate = "step3_gate"
    revise_loop = "revise_loop"
    persist = "persist"
    error = "error"


class ReportCreateText(BaseModel):
    title: str | None = None
    content: str = Field(..., min_length=1)
    filename: str | None = "report.txt"
    meta: dict[str, Any] = Field(default_factory=dict)


class ReportOut(BaseModel):
    report_id: str
    title: str | None = None
    filename: str
    size_bytes: int
    created_at: str
    meta: dict[str, Any] = Field(default_factory=dict)


class SeedFactorIn(BaseModel):
    factor_id: str
    name_zh: str
    name_en: str | None = None
    category: str | None = None
    formula_or_rule: str
    inputs: list[str] = Field(default_factory=list)
    economic_logic: str | None = None
    signal_direction: str | None = None
    source: str | None = None
    frequency: str | None = None


class JobCreate(BaseModel):
    report_id: str | None = None
    content: str | None = None
    title: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    max_round: int | None = None
    mcp_enabled: bool | None = None
    timeout_sec: int | None = None
    mode: Literal["extract", "evaluate"] | None = None
    factors: list[SeedFactorIn] | None = None

    @model_validator(mode="after")
    def _validate_evaluate(self) -> JobCreate:
        is_evaluate = self.mode == "evaluate" or bool(self.factors)
        if is_evaluate:
            if not self.factors:
                raise ValueError("evaluate 模式需要提供非空 factors")
            if self.mode is None:
                self.mode = "evaluate"
        return self


class JobProgress(BaseModel):
    phase: str = "queued"
    round: int = 0
    message: str = ""
    percent: int = 0


class JobSummary(BaseModel):
    job_id: str
    report_id: str | None = None
    batch_id: str | None = None
    status: JobStatus
    created_at: str
    updated_at: str
    progress: JobProgress = Field(default_factory=JobProgress)
    error: str | None = None
    rounds_used: int = 0
    saved_count: int = 0
    dropped_count: int = 0
    title: str | None = None


class BatchItemCreate(BaseModel):
    report_id: str | None = None
    content: str | None = None
    title: str | None = None
    filename: str | None = None


class BatchCreate(BaseModel):
    title: str | None = None
    items: list[BatchItemCreate] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    max_round: int | None = None
    mcp_enabled: bool | None = None
    timeout_sec: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class BatchStatusCounts(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    timed_out: int = 0


class BatchSummary(BaseModel):
    batch_id: str
    title: str | None = None
    status: str
    total: int = 0
    counts: BatchStatusCounts = Field(default_factory=BatchStatusCounts)
    created_at: str
    updated_at: str
    jobs: list[JobSummary] = Field(default_factory=list)
    percent: int = 0
    message: str = ""


class FactorFormula(BaseModel):
    factor_id: str
    name_zh: str
    name_en: str | None = None
    category: str | None = None
    formula_or_rule: str
    inputs: list[str] = Field(default_factory=list)
    frequency: str | None = None
    signal_direction: str | None = None
    economic_logic: str | None = None
    final_score: float | None = None
    median_score: float | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    status: str = "SAVE"


class StepSummary(BaseModel):
    step_id: str
    seq: int
    step_type: StepType
    title: str
    round: int = 0
    role_code: str | None = None
    status: str = "ok"
    created_at: str
    summary: str = ""
    factor_ids: list[str] = Field(default_factory=list)
    role_name: str | None = None
    factor_count: int = 0


class StepDetail(StepSummary):
    payload: dict[str, Any] = Field(default_factory=dict)


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    factors: list[FactorFormula] = Field(default_factory=list)
    dropped: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[StepSummary] = Field(default_factory=list)
    rounds_used: int = 0
    report_id: str | None = None
