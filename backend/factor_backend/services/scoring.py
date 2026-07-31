from __future__ import annotations

from typing import Any


def normalize_weights(weights: dict[str, Any] | None) -> dict[str, float]:
    if not weights:
        return {}
    out: dict[str, float] = {}
    for k, v in weights.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def weighted_total(
    subscores: dict[str, Any] | None,
    weights: dict[str, Any] | None,
    *,
    info_insufficient: bool = False,
    info_insufficient_cap: float = 65,
) -> int:
    """
    按提示词权重计算 0–100 整数分。
    公式：sum(subscore_i * weight_i) / sum(weights)
    子项缺失按 0 计。
    """
    w = normalize_weights(weights)
    subs = {str(k): float(v) for k, v in (subscores or {}).items() if _is_number(v)}
    if not w:
        # 无权重时回退平均或直接用 total-like 字段
        if "total" in subs:
            score = subs["total"]
        elif subs:
            score = sum(subs.values()) / len(subs)
        else:
            score = 0.0
    else:
        denom = sum(w.values()) or 1.0
        score = sum(subs.get(k, 0.0) * wt for k, wt in w.items()) / denom

    score = max(0.0, min(100.0, score))
    if info_insufficient:
        score = min(score, float(info_insufficient_cap))
    return int(round(score))


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def ensure_subscores(
    raw: dict[str, Any],
    weights: dict[str, Any] | None,
    *,
    fallback_total: float | None = None,
) -> dict[str, float]:
    """从模型输出整理子项；若只有 total_score，则按权重项均摊。"""
    w = normalize_weights(weights)
    keys = list(w.keys()) if w else ["Logic", "Edge", "Implementability", "Robustness", "Tradability", "Novelty", "RiskControl"]
    subs: dict[str, float] = {}
    raw_subs = raw.get("subscores") if isinstance(raw.get("subscores"), dict) else {}
    for k in keys:
        if k in raw_subs and _is_number(raw_subs[k]):
            subs[k] = max(0.0, min(100.0, float(raw_subs[k])))
        elif k in raw and _is_number(raw[k]):
            subs[k] = max(0.0, min(100.0, float(raw[k])))
    if subs:
        return subs
    base = fallback_total if fallback_total is not None else float(raw.get("total_score") or 50)
    base = max(0.0, min(100.0, base))
    return {k: base for k in keys}
