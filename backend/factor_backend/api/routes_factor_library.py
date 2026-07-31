from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from factor_backend.api.deps import require_api_token
from factor_backend.services.factor_library import get_library_factors, list_library_packs

router = APIRouter(
    prefix="/api/v1/factor-library",
    tags=["factor-library"],
    dependencies=[Depends(require_api_token)],
)


class LibraryPackOut(BaseModel):
    id: str
    name: str
    version: str | None = None
    count: int = 0
    paper: str | None = None
    description: str | None = None


class LibraryFactorOut(BaseModel):
    factor_id: str
    name_zh: str
    name_en: str | None = None
    category: str | None = None
    source: str | None = None
    formula_or_rule: str
    inputs: list[str] = Field(default_factory=list)
    status: str = "LIBRARY"
    tags: list[str] = Field(default_factory=list)


class LibraryFactorsResponse(BaseModel):
    pack_id: str
    name: str | None = None
    version: str | None = None
    paper: str | None = None
    total: int
    offset: int
    limit: int
    factors: list[LibraryFactorOut]


@router.get("/packs", response_model=list[LibraryPackOut])
def get_packs() -> list[LibraryPackOut]:
    return [LibraryPackOut(**p) for p in list_library_packs()]


@router.get("/{pack_id}/factors", response_model=LibraryFactorsResponse)
def get_pack_factors(
    pack_id: str,
    q: str | None = Query(default=None, description="按 ID/名称/公式搜索"),
    limit: int = Query(default=101, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LibraryFactorsResponse:
    try:
        data = get_library_factors(pack_id, q=q, limit=limit, offset=offset)
    except FileNotFoundError as e:
        raise HTTPException(404, f"未知因子库: {pack_id}") from e
    return LibraryFactorsResponse(**data)
