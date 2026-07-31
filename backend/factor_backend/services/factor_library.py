from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from factor_backend.config import repo_root


def _library_dir() -> Path:
    return repo_root() / "data" / "factor_library"


@lru_cache
def load_alpha101() -> dict[str, Any]:
    path = _library_dir() / "alpha101.json"
    if not path.exists():
        return {
            "name": "WorldQuant Alpha101",
            "version": "0",
            "count": 0,
            "factors": [],
            "error": f"missing {path}",
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    data["count"] = len(data.get("factors") or [])
    return data


def list_library_packs() -> list[dict[str, Any]]:
    alpha = load_alpha101()
    return [
        {
            "id": "alpha101",
            "name": alpha.get("name") or "Alpha101",
            "version": alpha.get("version"),
            "count": alpha.get("count", 0),
            "paper": alpha.get("paper"),
            "description": "WorldQuant 公开的 101 个公式化 Alpha（Kakushadze 2016）",
        }
    ]


def get_library_factors(
    pack_id: str = "alpha101",
    *,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    if pack_id != "alpha101":
        raise FileNotFoundError(pack_id)
    pack = load_alpha101()
    factors = list(pack.get("factors") or [])
    if q:
        qq = q.strip().lower()
        factors = [
            f
            for f in factors
            if qq in str(f.get("factor_id", "")).lower()
            or qq in str(f.get("name_zh", "")).lower()
            or qq in str(f.get("name_en", "")).lower()
            or qq in str(f.get("formula_or_rule", "")).lower()
        ]
    total = len(factors)
    sliced = factors[offset : offset + max(1, min(limit, 500))]
    return {
        "pack_id": pack_id,
        "name": pack.get("name"),
        "version": pack.get("version"),
        "paper": pack.get("paper"),
        "total": total,
        "offset": offset,
        "limit": limit,
        "factors": sliced,
    }
