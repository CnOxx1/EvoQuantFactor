from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from qfactor.settings import ProjectConfig, get_project_config


class Base(DeclarativeBase):
    pass


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"
    cal_date: Mapped[str] = mapped_column(String(8), primary_key=True)
    is_open: Mapped[int] = mapped_column(Integer, default=1)


class UniverseMember(Base):
    __tablename__ = "universe_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    universe: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_date: Mapped[str | None] = mapped_column(String(8), nullable=True)

    __table_args__ = (
        UniqueConstraint("universe", "trade_date", "ts_code", name="uq_univ_date_code"),
    )


class DailyBar(Base):
    __tablename__ = "daily_bars"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    vol: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_adj: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    circ_mv: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_bar_code_date"),
        Index("ix_bars_date_code", "trade_date", "ts_code"),
    )


class IndustryMap(Base):
    __tablename__ = "industry_map"
    ts_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DataVersion(Base):
    __tablename__ = "data_versions"
    data_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    synced_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    universe: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start: Mapped[str | None] = mapped_column(String(8), nullable=True)
    end: Mapped[str | None] = mapped_column(String(8), nullable=True)
    n_codes_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_industry: Mapped[bool] = mapped_column(Boolean, default=False)
    has_circ_mv: Mapped[bool] = mapped_column(Boolean, default=False)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class FactorRow(Base):
    __tablename__ = "factors"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    status: Mapped[str] = mapped_column(String(32), index=True, default="draft")
    family: Mapped[str] = mapped_column(String(64), default="price_volume")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mechanism: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_gate: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    expr_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    skeleton: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    rank_ic_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    icir: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_corr: Mapped[float | None] = mapped_column(Float, nullable=True)
    oos_ic_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_adjusted_ls: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FactorReport(Base):
    __tablename__ = "factor_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    gate_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), index=True)
    report_json: Mapped[str] = mapped_column(Text)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class JobRun(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)  # sync|loop|eval
    state: Mapped[str] = mapped_column(String(32), index=True)  # pending|running|done|error
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LoopCheckpoint(Base):
    __tablename__ = "loop_checkpoints"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(64))


class LibraryOpLog(Base):
    __tablename__ = "library_ops"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64))
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


def db_path(cfg: ProjectConfig | None = None) -> Path:
    cfg = cfg or get_project_config()
    rel = (cfg.project.get("paths") or {}).get("database", "data/qfactor.sqlite3")
    path = (cfg.root / rel).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache
def get_engine(db_url: str | None = None):
    cfg = get_project_config()
    url = db_url or f"sqlite:///{db_path(cfg).as_posix()}"
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return engine


def get_session_factory(db_url: str | None = None):
    return sessionmaker(bind=get_engine(db_url), autoflush=False, autocommit=False, future=True)


def init_db(db_url: str | None = None) -> Path:
    cfg = get_project_config()
    path = db_path(cfg) if db_url is None else Path(".")
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    return db_path(cfg)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
