from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from factor_backend.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ReportRow(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), default="report.txt")
    content: Mapped[str] = mapped_column(Text, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRow(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rounds_used: Mapped[int] = mapped_column(Integer, default=0)
    saved_count: Mapped[int] = mapped_column(Integer, default=0)
    dropped_count: Mapped[int] = mapped_column(Integer, default=0)
    step_seq: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=1800)
    lock_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BatchRow(Base):
    """一批多份研报任务。"""

    __tablename__ = "batches"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StepRow(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    step_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512), default="")
    round: Mapped[int] = mapped_column(Integer, default=0)
    role_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobResultRow(Base):
    __tablename__ = "job_results"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LlmConfigRow(Base):
    """前端可配置的 LLM 设置（单行，id 固定为 1）。"""

    __tablename__ = "llm_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    use_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    api_format: Mapped[str] = mapped_column(String(32), default="openai")
    base_url: Mapped[str] = mapped_column(String(512), default="https://api.openai.com/v1")
    api_key: Mapped[str] = mapped_column(Text, default="")
    model_step1: Mapped[str] = mapped_column(String(128), default="gpt-4o")
    model_review: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    timeout_sec: Mapped[float] = mapped_column(Float, default=120.0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptOverrideRow(Base):
    """前端可覆盖的提示词 / 权重。"""

    __tablename__ = "prompt_overrides"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    system: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    weights_json: Mapped[str] = mapped_column(Text, default="{}")
    scoring_json: Mapped[str] = mapped_column(Text, default="{}")
    mcp_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_session_factory():
    get_engine()
    return _SessionLocal


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _sqlite_migrate(engine)
    Session = get_session_factory()
    with Session() as db:
        row = db.get(LlmConfigRow, 1)
        if row is None:
            settings = get_settings()
            db.add(
                LlmConfigRow(
                    id=1,
                    enabled=True,
                    use_mock=settings.llm_mock,
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key or "",
                    model_step1=settings.llm_model_step1,
                    model_review=settings.llm_model_review,
                )
            )
            db.commit()


def _sqlite_migrate(engine) -> None:
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(jobs)")).fetchall()}
        if cols and "batch_id" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN batch_id VARCHAR(64)"))
        llm_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(llm_config)")).fetchall()}
        if llm_cols and "api_format" not in llm_cols:
            conn.execute(text("ALTER TABLE llm_config ADD COLUMN api_format VARCHAR(32) DEFAULT 'openai'"))
        # ensure batches table exists via create_all already



def reset_engine_for_tests(database_url: str) -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _engine = create_engine(database_url, future=True, connect_args=connect_args)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=_engine)
    _sqlite_migrate(_engine)
    with _SessionLocal() as db:
        if db.get(LlmConfigRow, 1) is None:
            db.add(LlmConfigRow(id=1, use_mock=True, api_key="", api_format="openai"))
            db.commit()
