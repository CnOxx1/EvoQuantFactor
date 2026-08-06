from __future__ import annotations

import json
import threading
import time
from typing import Any

from factor_backend.db.models import PromptOverrideRow, get_session_factory, utcnow
from factor_backend.services.prompt_loader import ROLE_FILES, PromptLoader


PROMPT_KEYS = ["step1_extract", "step1_optimize", *ROLE_FILES.keys(), "_shared_mcp", "news_summarize"]

_cache_lock = threading.Lock()
_file_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_merged_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SEC = 30.0


def invalidate_prompt_cache(key: str | None = None) -> None:
    with _cache_lock:
        if key is None:
            _file_cache.clear()
            _merged_cache.clear()
        else:
            _file_cache.pop(key, None)
            _merged_cache.pop(key, None)


def _file_default(key: str) -> dict[str, Any]:
    now = time.monotonic()
    with _cache_lock:
        hit = _file_cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_SEC:
            return dict(hit[1])

    loader = PromptLoader()
    if key in ("step1_extract", "step1_optimize", "news_summarize"):
        data = loader.load(f"{key}.json")
        out = {
            "key": key,
            "name": data.get("name", key),
            "system": data.get("system", ""),
            "user_template": data.get("user_template", ""),
            "weights": (data.get("scoring") or {}).get("weights") or {},
            "scoring": data.get("scoring") or {},
            "mcp": data.get("mcp") or {},
            "source": "file",
        }
    elif key == "_shared_mcp":
        data = loader.load("_shared_mcp.json")
        out = {
            "key": key,
            "name": data.get("name", key),
            "system": data.get("system_append", ""),
            "user_template": "",
            "weights": {},
            "scoring": {},
            "mcp": {"planned_tools": data.get("planned_tools", [])},
            "source": "file",
        }
    elif key in ROLE_FILES:
        data = loader.load(ROLE_FILES[key])
        out = {
            "key": key,
            "name": data.get("name", key),
            "system": data.get("system", ""),
            "user_template": data.get("user_template", ""),
            "weights": (data.get("scoring") or {}).get("weights") or {},
            "scoring": data.get("scoring") or {},
            "mcp": data.get("mcp") or {},
            "source": "file",
        }
    else:
        raise KeyError(key)

    with _cache_lock:
        _file_cache[key] = (now, out)
    return dict(out)


def get_prompt_config(key: str) -> dict[str, Any]:
    now = time.monotonic()
    with _cache_lock:
        hit = _merged_cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_SEC:
            return dict(hit[1])

    base = _file_default(key)
    Session = get_session_factory()
    with Session() as db:
        row = db.get(PromptOverrideRow, key)
        if not row or not row.enabled:
            out = base
        else:
            scoring = json.loads(row.scoring_json or "{}")
            weights = json.loads(row.weights_json or "{}") or (scoring.get("weights") or {})
            mcp = json.loads(row.mcp_json or "{}")
            out = {
                "key": key,
                "name": row.name or base["name"],
                "system": row.system if row.system is not None else base["system"],
                "user_template": row.user_template if row.user_template is not None else base["user_template"],
                "weights": weights or base["weights"],
                "scoring": {**base["scoring"], **scoring, "weights": weights or base["weights"]},
                "mcp": mcp or base["mcp"],
                "source": "db_override",
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }

    with _cache_lock:
        _merged_cache[key] = (now, out)
    return dict(out)


def list_prompt_configs() -> list[dict[str, Any]]:
    out = []
    for key in PROMPT_KEYS:
        try:
            cfg = get_prompt_config(key)
            out.append(
                {
                    "key": key,
                    "name": cfg["name"],
                    "source": cfg["source"],
                    "weights": cfg.get("weights") or {},
                    "has_system": bool(cfg.get("system")),
                    "mcp_prefer_tools": (cfg.get("mcp") or {}).get("prefer_tools") or [],
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return out


def upsert_prompt_config(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    if key not in PROMPT_KEYS:
        raise KeyError(f"unknown prompt key: {key}")
    base = _file_default(key)
    Session = get_session_factory()
    with Session() as db:
        row = db.get(PromptOverrideRow, key)
        if row is None:
            row = PromptOverrideRow(key=key, name=base["name"])
            db.add(row)
        if "name" in payload and payload["name"] is not None:
            row.name = str(payload["name"])
        if "system" in payload and payload["system"] is not None:
            row.system = str(payload["system"])
        if "user_template" in payload and payload["user_template"] is not None:
            row.user_template = str(payload["user_template"])
        if "weights" in payload and payload["weights"] is not None:
            row.weights_json = json.dumps(payload["weights"], ensure_ascii=False)
            scoring = json.loads(row.scoring_json or "{}")
            scoring["weights"] = payload["weights"]
            row.scoring_json = json.dumps(scoring, ensure_ascii=False)
        if "scoring" in payload and payload["scoring"] is not None:
            scoring = dict(payload["scoring"])
            if "weights" in payload and payload["weights"] is not None:
                scoring["weights"] = payload["weights"]
            row.scoring_json = json.dumps(scoring, ensure_ascii=False)
            if "weights" in scoring:
                row.weights_json = json.dumps(scoring["weights"], ensure_ascii=False)
        if "mcp" in payload and payload["mcp"] is not None:
            row.mcp_json = json.dumps(payload["mcp"], ensure_ascii=False)
        if "enabled" in payload and payload["enabled"] is not None:
            row.enabled = bool(payload["enabled"])
        row.updated_at = utcnow()
        db.commit()
    invalidate_prompt_cache(key)
    return get_prompt_config(key)


def reset_prompt_config(key: str) -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        row = db.get(PromptOverrideRow, key)
        if row:
            db.delete(row)
            db.commit()
    invalidate_prompt_cache(key)
    return get_prompt_config(key)


def role_runtime_prompt(role_code: str) -> dict[str, Any]:
    cfg = get_prompt_config(role_code)
    shared = ""
    if (cfg.get("mcp") or {}).get("append_shared", True):
        shared_cfg = get_prompt_config("_shared_mcp")
        shared = shared_cfg.get("system") or ""
    system = cfg.get("system") or ""
    if shared:
        system = system + "\n\n" + shared
    return {
        "role_code": role_code,
        "name": cfg.get("name") or role_code,
        "system": system,
        "user_template": cfg.get("user_template") or "",
        "scoring": cfg.get("scoring") or {},
        "weights": cfg.get("weights") or {},
        "mcp": cfg.get("mcp") or {},
        "source": cfg.get("source"),
    }


def step1_runtime_prompt(prompt_key: str = "step1_extract") -> dict[str, Any]:
    if prompt_key not in ("step1_extract", "step1_optimize"):
        prompt_key = "step1_extract"
    cfg = get_prompt_config(prompt_key)
    system = cfg.get("system") or ""
    if (cfg.get("mcp") or {}).get("append_shared", True):
        shared = get_prompt_config("_shared_mcp").get("system") or ""
        if shared:
            system = system + "\n\n" + shared
    return {
        "key": prompt_key,
        "system": system,
        "user_template": cfg.get("user_template") or "",
        "mcp": cfg.get("mcp") or {},
        "source": cfg.get("source"),
    }


def news_runtime_prompt() -> dict[str, Any]:
    """资讯摘要提示词（不追加 MCP 共用段）。"""
    cfg = get_prompt_config("news_summarize")
    return {
        "key": "news_summarize",
        "system": cfg.get("system") or "",
        "user_template": cfg.get("user_template") or "",
        "mcp": cfg.get("mcp") or {},
        "source": cfg.get("source"),
    }
