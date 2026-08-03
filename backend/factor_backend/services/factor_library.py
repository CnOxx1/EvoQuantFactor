from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from factor_backend.config import repo_root

_pack_lock = threading.Lock()


def _library_dir() -> Path:
    path = repo_root() / "data" / "factor_library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pack_path(pack_id: str) -> Path:
    return _library_dir() / f"{pack_id}.json"


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


def _empty_pack(*, name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1",
        "description": description,
        "updated_at": None,
        "factors": [],
        "count": 0,
    }


def _load_mutable_pack(
    pack_id: str,
    *,
    name: str,
    description: str,
) -> dict[str, Any]:
    path = _pack_path(pack_id)
    empty = _empty_pack(name=name, description=description)
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("name", name)
    data.setdefault("version", "1")
    data.setdefault("description", description)
    data.setdefault("factors", [])
    data["count"] = len(data.get("factors") or [])
    return data


def _save_mutable_pack(pack_id: str, data: dict[str, Any]) -> dict[str, Any]:
    path = _pack_path(pack_id)
    data = dict(data)
    data["count"] = len(data.get("factors") or [])
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_workspace() -> dict[str, Any]:
    return _load_mutable_pack(
        "workspace",
        name="任务入库",
        description="研报分析 / 优化任务过线保存的因子（SAVE）",
    )


def load_dropped() -> dict[str, Any]:
    return _load_mutable_pack(
        "dropped",
        name="淘汰库",
        description="门槛裁决淘汰的因子（DROP），保留公式与淘汰原因便于复盘/再优化",
    )


def extract_formula(fobj: dict[str, Any] | None) -> str | None:
    if not isinstance(fobj, dict):
        return None
    definition = fobj.get("definition")
    if isinstance(definition, dict):
        formula = definition.get("formula_or_rule") or definition.get("formula")
        if formula:
            return str(formula).strip() or None
    elif isinstance(definition, str) and definition.strip():
        return definition.strip()
    for key in ("formula_or_rule", "formula", "calculation"):
        val = fobj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_inputs(fobj: dict[str, Any] | None) -> list[str]:
    if not isinstance(fobj, dict):
        return []
    definition = fobj.get("definition")
    if isinstance(definition, dict):
        inputs = definition.get("inputs") or []
        if isinstance(inputs, list):
            return [str(x) for x in inputs]
    inputs = fobj.get("inputs") or []
    if isinstance(inputs, list):
        return [str(x) for x in inputs]
    return []


def _library_row_from_factor(
    *,
    job_id: str,
    fobj: dict[str, Any],
    status: str,
    reason: str | None = None,
) -> dict[str, Any] | None:
    origin_id = str(fobj.get("factor_id") or "").strip()
    if not origin_id:
        return None
    formula = extract_formula(fobj)
    if not formula:
        return None
    name_zh = str(fobj.get("name_zh") or fobj.get("name") or origin_id)
    return {
        "factor_id": f"{job_id}:{origin_id}",
        "origin_factor_id": origin_id,
        "job_id": job_id,
        "name_zh": name_zh,
        "name_en": fobj.get("name_en"),
        "category": fobj.get("category") or ("淘汰因子" if status == "DROP" else "任务产出"),
        "source": f"job:{job_id}",
        "formula_or_rule": formula,
        "inputs": extract_inputs(fobj),
        "economic_logic": fobj.get("economic_logic"),
        "signal_direction": fobj.get("signal_direction"),
        "frequency": (
            (fobj.get("definition") or {}).get("frequency")
            if isinstance(fobj.get("definition"), dict)
            else fobj.get("frequency")
        ),
        "final_score": fobj.get("final_score"),
        "median_score": fobj.get("median_score"),
        "status": status,
        "reason": reason,
        "tags": ["job", status.lower()],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _upsert_rows(pack_id: str, *, name: str, description: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return _load_mutable_pack(pack_id, name=name, description=description)
    with _pack_lock:
        data = _load_mutable_pack(pack_id, name=name, description=description)
        by_id = {
            str(x.get("factor_id")): x
            for x in (data.get("factors") or [])
            if isinstance(x, dict) and x.get("factor_id")
        }
        for row in rows:
            by_id[str(row["factor_id"])] = row
        data["name"] = name
        data["description"] = description
        data["factors"] = sorted(
            by_id.values(),
            key=lambda x: str(x.get("updated_at") or ""),
            reverse=True,
        )
        return _save_mutable_pack(pack_id, data)


def upsert_job_factors_to_workspace(
    *,
    job_id: str,
    saved: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把过线因子写入任务入库。candidates 参数保留兼容，实际写入淘汰库。"""
    rows: list[dict[str, Any]] = []
    for f in saved or []:
        row = _library_row_from_factor(job_id=job_id, fobj=f, status="SAVE", reason=f.get("reason"))
        if row:
            rows.append(row)
    workspace = _upsert_rows(
        "workspace",
        name="任务入库",
        description="研报分析 / 优化任务过线保存的因子（SAVE）",
        rows=rows,
    )
    if candidates:
        upsert_job_factors_to_dropped(job_id=job_id, dropped=candidates)
    return workspace


def upsert_job_factors_to_dropped(
    *,
    job_id: str,
    dropped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把淘汰因子写入淘汰库。"""
    rows: list[dict[str, Any]] = []
    for f in dropped or []:
        row = _library_row_from_factor(
            job_id=job_id,
            fobj=f,
            status="DROP",
            reason=f.get("reason") or f.get("drop_reason") or "门槛淘汰",
        )
        if row:
            rows.append(row)
    return _upsert_rows(
        "dropped",
        name="淘汰库",
        description="门槛裁决淘汰的因子（DROP），保留公式与淘汰原因便于复盘/再优化",
        rows=rows,
    )


def list_library_packs() -> list[dict[str, Any]]:
    alpha = load_alpha101()
    workspace = load_workspace()
    dropped = load_dropped()
    return [
        {
            "id": "alpha101",
            "name": alpha.get("name") or "Alpha101",
            "version": alpha.get("version"),
            "count": alpha.get("count", 0),
            "paper": alpha.get("paper"),
            "description": "WorldQuant 公开的 101 个公式化 Alpha（Kakushadze 2016）",
        },
        {
            "id": "workspace",
            "name": workspace.get("name") or "任务入库",
            "version": workspace.get("version"),
            "count": workspace.get("count", 0),
            "paper": None,
            "description": workspace.get("description") or "过线保存的任务因子",
        },
        {
            "id": "dropped",
            "name": dropped.get("name") or "淘汰库",
            "version": dropped.get("version"),
            "count": dropped.get("count", 0),
            "paper": None,
            "description": dropped.get("description") or "淘汰因子及原因",
        },
    ]


def get_library_factors(
    pack_id: str = "alpha101",
    *,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    if pack_id == "alpha101":
        pack = load_alpha101()
    elif pack_id == "workspace":
        pack = load_workspace()
    elif pack_id == "dropped":
        pack = load_dropped()
    else:
        raise FileNotFoundError(pack_id)

    factors = list(pack.get("factors") or [])
    if q:
        qq = q.strip().lower()
        factors = [
            f
            for f in factors
            if qq in str(f.get("factor_id", "")).lower()
            or qq in str(f.get("origin_factor_id", "")).lower()
            or qq in str(f.get("name_zh", "")).lower()
            or qq in str(f.get("name_en", "")).lower()
            or qq in str(f.get("formula_or_rule", "")).lower()
            or qq in str(f.get("job_id", "")).lower()
            or qq in str(f.get("source", "")).lower()
            or qq in str(f.get("reason", "")).lower()
            or qq in str(f.get("status", "")).lower()
        ]
    total = len(factors)
    sliced = factors[offset : offset + max(1, min(limit, 500))]
    out_factors = []
    for f in sliced:
        if not isinstance(f, dict):
            continue
        formula = f.get("formula_or_rule") or extract_formula(f) or ""
        out_factors.append(
            {
                "factor_id": str(f.get("factor_id")),
                "name_zh": f.get("name_zh") or str(f.get("factor_id")),
                "name_en": f.get("name_en"),
                "category": f.get("category"),
                "source": f.get("source"),
                "formula_or_rule": formula,
                "inputs": f.get("inputs") or [],
                "status": f.get("status") or "LIBRARY",
                "tags": f.get("tags") or [],
                "job_id": f.get("job_id"),
                "origin_factor_id": f.get("origin_factor_id"),
                "final_score": f.get("final_score"),
                "economic_logic": f.get("economic_logic"),
                "signal_direction": f.get("signal_direction"),
                "frequency": f.get("frequency"),
                "reason": f.get("reason"),
            }
        )
    return {
        "pack_id": pack_id,
        "name": pack.get("name"),
        "version": pack.get("version"),
        "paper": pack.get("paper"),
        "total": total,
        "offset": offset,
        "limit": limit,
        "factors": out_factors,
    }
