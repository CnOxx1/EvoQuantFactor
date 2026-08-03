from __future__ import annotations

from dataclasses import dataclass

from factor_backend.config import get_settings
from factor_backend.db.models import LlmConfigRow, get_session_factory, utcnow

ALLOWED_API_FORMATS = frozenset({"openai", "anthropic", "cursor"})


@dataclass
class LlmRuntimeConfig:
    enabled: bool
    use_mock: bool
    api_format: str
    base_url: str
    api_key: str
    model_step1: str
    model_review: str
    timeout_sec: float
    max_retries: int

    @property
    def should_call_llm(self) -> bool:
        return bool(self.enabled and not self.use_mock and self.api_key)


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:3] + "*" * (len(key) - 7) + key[-4:]


def _normalize_api_format(value: str | None) -> str:
    fmt = (value or "openai").strip().lower()
    if fmt not in ALLOWED_API_FORMATS:
        return "openai"
    return fmt


def get_llm_config() -> LlmRuntimeConfig:
    settings = get_settings()
    Session = get_session_factory()
    with Session() as db:
        row = db.get(LlmConfigRow, 1)
        if row is None:
            return LlmRuntimeConfig(
                enabled=True,
                use_mock=settings.use_mock_llm,
                api_format="openai",
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model_step1=settings.llm_model_step1,
                model_review=settings.llm_model_review,
                timeout_sec=300.0,
                max_retries=2,
            )
        return LlmRuntimeConfig(
            enabled=bool(row.enabled),
            use_mock=bool(row.use_mock),
            api_format=_normalize_api_format(getattr(row, "api_format", None)),
            base_url=row.base_url or settings.llm_base_url,
            api_key=row.api_key or "",
            model_step1=row.model_step1 or settings.llm_model_step1,
            model_review=row.model_review or settings.llm_model_review,
            timeout_sec=float(row.timeout_sec or 300),
            max_retries=int(row.max_retries or 2),
        )


def upsert_llm_config(payload: dict) -> LlmRuntimeConfig:
    Session = get_session_factory()
    with Session() as db:
        row = db.get(LlmConfigRow, 1)
        if row is None:
            row = LlmConfigRow(id=1)
            db.add(row)
        if "enabled" in payload and payload["enabled"] is not None:
            row.enabled = bool(payload["enabled"])
        if "use_mock" in payload and payload["use_mock"] is not None:
            row.use_mock = bool(payload["use_mock"])
        if "api_format" in payload and payload["api_format"] is not None:
            row.api_format = _normalize_api_format(str(payload["api_format"]))
        if "base_url" in payload and payload["base_url"] is not None:
            row.base_url = str(payload["base_url"]).rstrip("/")
        if "api_key" in payload and payload["api_key"] is not None and payload["api_key"] != "":
            # 空字符串表示不更新；前端可用 "***" 表示保持原样
            if payload["api_key"] not in ("***", "unchanged", mask_api_key(row.api_key)):
                row.api_key = str(payload["api_key"])
        if "model_step1" in payload and payload["model_step1"] is not None:
            row.model_step1 = str(payload["model_step1"])
        if "model_review" in payload and payload["model_review"] is not None:
            row.model_review = str(payload["model_review"])
        if "timeout_sec" in payload and payload["timeout_sec"] is not None:
            row.timeout_sec = float(payload["timeout_sec"])
        if "max_retries" in payload and payload["max_retries"] is not None:
            row.max_retries = int(payload["max_retries"])
        row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
        return get_llm_config()


def llm_config_public_dict() -> dict:
    cfg = get_llm_config()
    return {
        "enabled": cfg.enabled,
        "use_mock": cfg.use_mock,
        "api_format": cfg.api_format,
        "base_url": cfg.base_url,
        "api_key_set": bool(cfg.api_key),
        "api_key_masked": mask_api_key(cfg.api_key),
        "model_step1": cfg.model_step1,
        "model_review": cfg.model_review,
        "timeout_sec": cfg.timeout_sec,
        "max_retries": cfg.max_retries,
        "should_call_llm": cfg.should_call_llm,
    }
