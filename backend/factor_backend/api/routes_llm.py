from __future__ import annotations

from fastapi import APIRouter, Depends

from factor_backend.api.deps import require_api_token
from factor_backend.llm.client import LlmClient, LlmError
from factor_backend.models.llm_schemas import LlmConfigOut, LlmConfigUpdate, LlmTestOut
from factor_backend.services.llm_config import (
    LlmRuntimeConfig,
    get_llm_config,
    llm_config_public_dict,
    upsert_llm_config,
)

router = APIRouter(prefix="/api/v1/llm", tags=["llm"], dependencies=[Depends(require_api_token)])


def _merge_runtime(base: LlmRuntimeConfig, override: LlmConfigUpdate | None) -> LlmRuntimeConfig:
    """用请求体中的草稿配置覆盖当前运行时配置（不落库）。"""
    if override is None:
        return base
    data = override.model_dump(exclude_unset=True)
    api_key = base.api_key
    if "api_key" in data and data["api_key"]:
        key = str(data["api_key"])
        if key not in ("***", "unchanged"):
            api_key = key
    return LlmRuntimeConfig(
        enabled=bool(data["enabled"]) if "enabled" in data else base.enabled,
        use_mock=bool(data["use_mock"]) if "use_mock" in data else base.use_mock,
        api_format=str(data["api_format"]) if "api_format" in data else base.api_format,
        base_url=str(data["base_url"]).rstrip("/") if "base_url" in data and data["base_url"] else base.base_url,
        api_key=api_key,
        model_step1=str(data["model_step1"]) if "model_step1" in data and data["model_step1"] else base.model_step1,
        model_review=str(data["model_review"]) if "model_review" in data and data["model_review"] else base.model_review,
        timeout_sec=float(data["timeout_sec"]) if "timeout_sec" in data and data["timeout_sec"] is not None else base.timeout_sec,
        max_retries=int(data["max_retries"]) if "max_retries" in data and data["max_retries"] is not None else base.max_retries,
    )


@router.get("/config", response_model=LlmConfigOut)
def get_config() -> LlmConfigOut:
    """前端读取当前 LLM 配置（api_key 脱敏）。"""
    return LlmConfigOut(**llm_config_public_dict())


@router.put("/config", response_model=LlmConfigOut)
def put_config(body: LlmConfigUpdate) -> LlmConfigOut:
    """前端保存 LLM 配置。"""
    upsert_llm_config(body.model_dump(exclude_unset=True))
    return LlmConfigOut(**llm_config_public_dict())


@router.post("/test", response_model=LlmTestOut)
async def test_llm(body: LlmConfigUpdate | None = None) -> LlmTestOut:
    """用当前配置（可叠加请求体草稿）探测 LLM 是否可用。"""
    cfg = _merge_runtime(get_llm_config(), body)
    if cfg.use_mock:
        return LlmTestOut(
            ok=False,
            message="当前为 Mock 模式，未发起真实请求。请关闭 Mock 后再测连通。",
            detail={"mode": "mock", "api_format": cfg.api_format},
        )
    if not cfg.api_key:
        return LlmTestOut(
            ok=False,
            message="未配置 api_key（可先填写 Key 再点测试，无需先保存）",
            detail={"api_format": cfg.api_format},
        )
    try:
        detail = await LlmClient(cfg).test_connection()
        return LlmTestOut(ok=True, message="连通成功", detail=detail)
    except LlmError as e:
        return LlmTestOut(
            ok=False,
            message=str(e),
            detail={"api_format": cfg.api_format, "base_url": cfg.base_url},
        )
