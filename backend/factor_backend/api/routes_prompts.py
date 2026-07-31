from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from factor_backend.api.deps import require_api_token
from factor_backend.models.prompt_schemas import PromptConfigOut, PromptConfigUpdate, PromptSummary
from factor_backend.services.prompt_config import (
    get_prompt_config,
    list_prompt_configs,
    reset_prompt_config,
    upsert_prompt_config,
)

router = APIRouter(
    prefix="/api/v1/prompts",
    tags=["prompts"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[PromptSummary])
def list_prompts() -> list[PromptSummary]:
    """前端：列出 step1 / R1-R6 / shared 的权重与来源。"""
    return [PromptSummary(**x) for x in list_prompt_configs()]


@router.get("/{key}", response_model=PromptConfigOut)
def get_prompt(key: str) -> PromptConfigOut:
    try:
        return PromptConfigOut(**get_prompt_config(key))
    except KeyError as e:
        raise HTTPException(404, f"unknown prompt key: {key}") from e


@router.put("/{key}", response_model=PromptConfigOut)
def put_prompt(key: str, body: PromptConfigUpdate) -> PromptConfigOut:
    """前端：更新提示词正文与评分权重。"""
    try:
        data = upsert_prompt_config(key, body.model_dump(exclude_unset=True))
        return PromptConfigOut(**data)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/{key}/reset", response_model=PromptConfigOut)
def reset_prompt(key: str) -> PromptConfigOut:
    """恢复为 prompts/ 目录默认文件。"""
    try:
        return PromptConfigOut(**reset_prompt_config(key))
    except KeyError as e:
        raise HTTPException(404, f"unknown prompt key: {key}") from e
