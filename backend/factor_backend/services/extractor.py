from __future__ import annotations

import re
from typing import Any


def _snippets(text: str, limit: int = 8) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # prefer lines that look like definitions / indicators
    scored: list[tuple[int, str]] = []
    keys = ("因子", "指标", "公式", "ROE", "PE", "换手", "动量", "波动", "市值", "成交额", "估值", "增速")
    for ln in lines:
        score = sum(1 for k in keys if k.lower() in ln.lower())
        if score:
            scored.append((score, ln[:240]))
    scored.sort(key=lambda x: (-x[0], -len(x[1])))
    out = [s for _, s in scored[:limit]]
    if not out:
        out = lines[: min(5, len(lines))]
    return out


def mock_extract_factors(report: str, revise_packet: dict[str, Any] | None = None) -> dict[str, Any]:
    """无 LLM 时的可运行提取器：从研报文本启发式生成结构化因子。"""
    if revise_packet and revise_packet.get("items"):
        return _mock_revise(report, revise_packet)

    snippets = _snippets(report)
    factors: list[dict[str, Any]] = []

    templates = [
        {
            "name_zh": "换手率动量",
            "name_en": "TurnoverMomentum",
            "category": "动量",
            "formula": "turnover_20d_mean / turnover_60d_mean - 1",
            "inputs": ["turnover"],
            "direction": "值越大越好",
            "freq": "日",
            "logic": "短期换手相对长期抬升，反映交易活跃与关注度上升。",
        },
        {
            "name_zh": "估值分位数",
            "name_en": "PEQuantile",
            "category": "价值",
            "formula": "1 - percentile_rank(pe_ttm, industry, 3y)",
            "inputs": ["pe_ttm", "industry"],
            "direction": "值越大越好（偏低估）",
            "freq": "日",
            "logic": "行业内估值处于历史低分位时，具备均值回归空间。",
        },
        {
            "name_zh": "盈利质量ROE",
            "name_en": "ROEQuality",
            "category": "质量",
            "formula": "roe_ttm - roe_ttm.industry_median",
            "inputs": ["roe_ttm", "industry"],
            "direction": "值越大越好",
            "freq": "季",
            "logic": "相对行业的盈利能力溢价更可持续。",
        },
    ]

    # create 2~3 factors depending on report length / keywords
    n = 2 if len(report) < 800 else 3
    for i, tpl in enumerate(templates[:n], start=1):
        quote = snippets[i - 1] if i - 1 < len(snippets) else snippets[0] if snippets else "（研报未定位到明确原句，标记为推断）"
        inferred = "推断" in quote or quote.startswith("（")
        factors.append(
            {
                "factor_id": f"F{i:02d}",
                "name_zh": tpl["name_zh"],
                "name_en": tpl["name_en"],
                "category": tpl["category"],
                "explicit_or_implicit": "隐式" if inferred else "显式",
                "status": "NEW",
                "economic_logic": tpl["logic"],
                "source_quote": quote,
                "definition": {
                    "inputs": tpl["inputs"],
                    "formula_or_rule": tpl["formula"],
                    "processing": "未提及" if inferred else "按研报口径，缺失细节标注未提及",
                    "frequency": tpl["freq"],
                },
                "signal_direction": tpl["direction"],
                "portfolio_construction": "推断：截面排序多空",
                "universe": "未提及",
                "holding_turnover": "未提及",
                "risks": "口径缺失、样本偏差、拥挤交易",
                "implementability_1_to_5": 4 if not inferred else 3,
                "priority": "高" if i == 1 else "中",
                "change_log": None,
                "evidence_quotes": [quote],
                "unresolved": [],
                "mcp_evidence": [],
                "data_check": "skipped",
            }
        )

    overview = {
        "title": _guess_title(report),
        "institution_author_date": "未提及",
        "coverage": "未提及",
        "report_type": "策略/主题（启发式）",
        "one_liner": "基于研报关键词启发式提取候选因子（mock/无LLM模式）",
    }
    return {
        "report_overview": overview,
        "core_logic": snippets[:3] or ["研报逻辑需人工确认"],
        "factors": factors,
        "non_factors": [],
        "changed_ids": [f["factor_id"] for f in factors],
        "downgraded_ids": [],
        "data_checks": {"summary": "mock extract; MCP skipped"},
    }


def _guess_title(report: str) -> str:
    for ln in report.splitlines():
        s = ln.strip()
        if 4 <= len(s) <= 80:
            return s
    return "未命名研报"


def _mock_revise(report: str, revise_packet: dict[str, Any]) -> dict[str, Any]:
    base = mock_extract_factors(report, revise_packet=None)
    by_id = {f["factor_id"]: f for f in base["factors"]}
    changed = []
    for item in revise_packet.get("items", []):
        fid = item.get("factor_id")
        if fid not in by_id:
            # keep creating refined version of first
            fid = next(iter(by_id))
        f = by_id[fid]
        f["status"] = "REFINED"
        f["definition"]["processing"] = "行业与市值中性化（修订轮补全）"
        f["definition"]["formula_or_rule"] = f["definition"]["formula_or_rule"] + " | zscore_cs"
        f["implementability_1_to_5"] = min(5, int(f.get("implementability_1_to_5", 3)) + 1)
        f["change_log"] = f"根据评审回灌补全中性化与标准化；gaps={item.get('main_gaps')}"
        f["unresolved"] = []
        changed.append(fid)
        by_id[fid] = f
    base["factors"] = list(by_id.values())
    base["changed_ids"] = changed
    base["report_overview"]["one_liner"] = "修订轮：已针对低分因子补全口径"
    return base


def richness_score(factor: dict[str, Any]) -> float:
    """0~100 启发式完整度，供 mock 评审使用。"""
    d = factor.get("definition") or {}
    score = 40.0
    if d.get("formula_or_rule"):
        score += 15
    if d.get("inputs"):
        score += 10
    if d.get("frequency") and d.get("frequency") != "未提及":
        score += 8
    if factor.get("signal_direction"):
        score += 7
    if factor.get("economic_logic"):
        score += 8
    if factor.get("source_quote") and "推断" not in str(factor.get("source_quote")):
        score += 6
    if factor.get("status") == "REFINED":
        score += 10
    if (d.get("processing") or "") not in ("", "未提及"):
        score += 8
    return max(0.0, min(100.0, score))
