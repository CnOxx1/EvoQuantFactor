from __future__ import annotations

import asyncio
from typing import Any

from factor_backend.services.extractor import richness_score

ROLES = [
    ("R1", "量化研究员", {"Implementability": 1.1, "Robustness": 1.05}),
    ("R2", "权益基金经理", {"Edge": 1.1, "Tradability": 1.05}),
    ("R3", "风控官", {"RiskControl": 1.15, "Robustness": 1.05}),
    ("R4", "卖方策略分析师", {"Logic": 1.1, "Novelty": 1.05}),
    ("R5", "数据工程师", {"Implementability": 1.2}),
    ("R6", "买方研究总监", {"Novelty": 1.1, "Edge": 1.05}),
]


def _clamp(x: float) -> int:
    return int(max(0, min(100, round(x))))


async def review_factor_mock(role_code: str, role_name: str, factor: dict[str, Any], bias: dict[str, float]) -> dict[str, Any]:
    await asyncio.sleep(0)  # yield for true concurrency later
    base = richness_score(factor)
    # slight role differentiation
    adj = 0.0
    if role_code == "R5" and factor.get("data_check") in ("failed", "data_unavailable"):
        adj -= 15
    if role_code == "R3" and "拥挤" in str(factor.get("risks", "")):
        adj -= 5
    if factor.get("status") == "REFINED":
        adj += 8
    # hash-like stable offset by role
    adj += (sum(ord(c) for c in role_code + factor.get("factor_id", "")) % 7) - 3

    total = _clamp(base + adj)
    veto = False
    veto_reason = None
    formula = (factor.get("definition") or {}).get("formula_or_rule") or ""
    if not formula.strip():
        veto = True
        veto_reason = "缺少可计算的公式/规则"
        total = min(total, 40)

    comment = (
        f"{role_name}视角：完整度约 {base:.0f}。公式「{formula[:80]}」。"
        f"{'已修订增强。' if factor.get('status')=='REFINED' else ''}"
        f"{'建议补中性化与字段口径。' if total < 80 else '口径基本可进入回测池。'}"
    )
    suggestions = []
    if total < 85:
        suggestions.append("补全中性化/去极值与更新频率")
    if "未提及" in str((factor.get("definition") or {}).get("processing", "")):
        suggestions.append("写明处理细节或标注推断")

    return {
        "role_code": role_code,
        "role_name": role_name,
        "factor_id": factor.get("factor_id"),
        "total_score": total,
        "comment": comment,
        "suggestions": suggestions,
        "veto": veto,
        "veto_reason": veto_reason,
        "mcp_evidence": [],
        "data_unavailable": False,
        "subscores": {"Logic": total, "Edge": total, "Implementability": total},
    }


async def review_all_roles_mock(factors: list[dict[str, Any]], factor_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """返回 scorecards[factor_id][role_code] = review."""
    ids = set(factor_ids) if factor_ids is not None else {f["factor_id"] for f in factors}
    targets = [f for f in factors if f["factor_id"] in ids]
    scorecards: dict[str, dict[str, Any]] = {f["factor_id"]: {} for f in targets}

    async def one(role_code: str, role_name: str, bias: dict[str, float], factor: dict[str, Any]):
        rev = await review_factor_mock(role_code, role_name, factor, bias)
        return factor["factor_id"], role_code, rev

    tasks = [
        one(code, name, bias, f)
        for f in targets
        for code, name, bias in ROLES
    ]
    results = await asyncio.gather(*tasks)
    for fid, role_code, rev in results:
        scorecards[fid][role_code] = rev
    return scorecards
