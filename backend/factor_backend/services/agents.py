from __future__ import annotations

import asyncio
import json
from typing import Any

from factor_backend.llm.client import LlmClient, LlmError
from factor_backend.mcp.client import collect_mcp_evidence
from factor_backend.services.extractor import mock_extract_factors, richness_score
from factor_backend.services.llm_config import LlmRuntimeConfig, get_llm_config
from factor_backend.services.llm_retry import is_retryable_llm_error, retry_async
from factor_backend.services.prompt_config import role_runtime_prompt, step1_runtime_prompt
from factor_backend.services.prompt_loader import ROLE_FILES
from factor_backend.services.reviewers import ROLES
from factor_backend.services.scoring import ensure_subscores, weighted_total


def _fill_template(tpl: str, mapping: dict[str, Any]) -> str:
    out = tpl
    for k, v in mapping.items():
        if isinstance(v, (dict, list)):
            val = json.dumps(v, ensure_ascii=False, indent=2)
        else:
            val = "" if v is None else str(v)
        out = out.replace("{{" + k + "}}", val)
    return out


def _slim_factor_for_prompt(f: dict[str, Any]) -> dict[str, Any]:
    """压缩因子体积，避免修订轮 prompt 过大导致 LLM 长时间生成。"""
    definition = f.get("definition")
    if isinstance(definition, dict):
        definition = {
            "inputs": definition.get("inputs") or [],
            "formula_or_rule": definition.get("formula_or_rule") or definition.get("formula"),
            "processing": definition.get("processing"),
            "frequency": definition.get("frequency"),
        }
    return {
        "factor_id": f.get("factor_id"),
        "name_zh": f.get("name_zh") or f.get("name"),
        "name_en": f.get("name_en"),
        "category": f.get("category"),
        "status": f.get("status"),
        "economic_logic": (f.get("economic_logic") or "")[:400],
        "definition": definition,
        "signal_direction": f.get("signal_direction"),
        "change_log": f.get("change_log"),
        "unresolved": (f.get("unresolved") or [])[:5],
    }


def _prepare_step1_context(
    *,
    report: str,
    revise_packet: dict[str, Any] | None,
    prev_factors: list[dict[str, Any]] | None,
    frozen_factors: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    report_limit = 40000 if revise_packet else 80000
    report_s = (report or "")[:report_limit]
    frozen_s = [_slim_factor_for_prompt(f) for f in (frozen_factors or []) if isinstance(f, dict)]
    if revise_packet:
        target_ids = {
            str(it.get("factor_id"))
            for it in (revise_packet.get("items") or [])
            if isinstance(it, dict) and it.get("factor_id") is not None
        }
        prev = [
            _slim_factor_for_prompt(f)
            for f in (prev_factors or [])
            if isinstance(f, dict) and str(f.get("factor_id")) in target_ids
        ]
        # 回灌包也瘦身，去掉完整 role_feedback 长文本可保留 suggestions
        slim_items = []
        for it in revise_packet.get("items") or []:
            if not isinstance(it, dict):
                continue
            feedback = []
            for fb in it.get("role_feedback") or []:
                if not isinstance(fb, dict):
                    continue
                feedback.append(
                    {
                        "role": fb.get("role"),
                        "score": fb.get("score"),
                        "comment": (fb.get("comment") or "")[:300],
                        "suggestions": (fb.get("suggestions") or [])[:4],
                        "veto": fb.get("veto", False),
                    }
                )
            slim_items.append(
                {
                    "factor_id": it.get("factor_id"),
                    "final_score": it.get("final_score"),
                    "median_score": it.get("median_score"),
                    "main_gaps": (it.get("main_gaps") or [])[:5],
                    "reason": it.get("reason"),
                    "role_feedback": feedback,
                    "revise_goals": it.get("revise_goals") or [],
                }
            )
        packet = {
            "needed": revise_packet.get("needed", True),
            "instruction": revise_packet.get("instruction"),
            "items": slim_items,
        }
        return report_s, prev, frozen_s, packet
    prev = [_slim_factor_for_prompt(f) for f in (prev_factors or []) if isinstance(f, dict)]
    return report_s, prev, frozen_s, None


async def run_step1_extract(
    *,
    report: str,
    revise_packet: dict[str, Any] | None,
    round_idx: int,
    meta: dict[str, Any] | None = None,
    frozen_factors: list[dict[str, Any]] | None = None,
    prev_factors: list[dict[str, Any]] | None = None,
    cfg: LlmRuntimeConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_llm_config()
    meta = meta or {}
    optimize_mode = meta.get("mode") == "evaluate"
    prompt_key = "step1_optimize" if optimize_mode else "step1_extract"

    if not cfg.should_call_llm:
        if cfg.use_mock or not cfg.enabled:
            return mock_extract_factors(report, revise_packet)
        raise LlmError("LLM 未就绪：请在 /api/v1/llm/config 配置 api_key，并设置 use_mock=false")

    report_s, prev_s, frozen_s, packet_s = _prepare_step1_context(
        report=report,
        revise_packet=revise_packet,
        prev_factors=prev_factors,
        frozen_factors=frozen_factors,
    )
    prompt = step1_runtime_prompt(prompt_key)
    user = _fill_template(
        prompt.get("user_template", ""),
        {
            "round": round_idx,
            "market": meta.get("market", ""),
            "symbols_hint": meta.get("symbols_hint", ""),
            "date_range_hint": meta.get("date_range_hint", ""),
            "report": report_s,
            "prev_factors": prev_s,
            "saved_factors": frozen_s,
            "revise_packet": packet_s,
        },
    )
    system = (
        prompt["system"]
        + "\n\n请严格输出 JSON 对象，字段需包含 report_overview, core_logic, factors, non_factors, changed_ids, data_checks。"
        + "每个 factor 必须包含 category。"
    )
    if revise_packet:
        n_items = len((revise_packet.get("items") or []))
        system += (
            f"\n本轮为修订：只改回灌包中的 {n_items} 个因子；输出 factors 至少覆盖这些 id；"
            "公式与口径尽量简练，避免超长叙述。"
        )
    client = LlmClient(cfg)
    attempts = max(1, int(cfg.max_retries or 2) + 1)

    async def _once() -> dict[str, Any]:
        result = await client.chat_json(system=system, user=user, model=cfg.model_step1)
        if not isinstance(result, dict) or "factors" not in result:
            raise LlmError("Step1 LLM 返回缺少 factors")
        return result

    result = await retry_async(
        _once,
        attempts=attempts,
        label=f"Step1[{prompt_key}](round={round_idx})",
        retryable=is_retryable_llm_error,
    )
    for f in result.get("factors") or []:
        if isinstance(f, dict) and not f.get("category"):
            f["category"] = "优化因子" if optimize_mode else "其他"
    result.setdefault("changed_ids", [f.get("factor_id") for f in result.get("factors", [])])
    result.setdefault("non_factors", [])
    result.setdefault("data_checks", {"summary": "llm", "prompt_key": prompt_key})
    result["_prompt_key"] = prompt_key
    return result


def _format_score_anchors(scoring: dict[str, Any] | None) -> str:
    anchors = (scoring or {}).get("anchors") or {}
    if not isinstance(anchors, dict) or not anchors:
        return ""
    lines = ["# 总分锚点（对齐你的综合判断；服务端仍按权重重算 total_score）"]
    for k, v in anchors.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


async def _review_one_role_live(
    *,
    role_code: str,
    targets: list[dict[str, Any]],
    factor_ids: list[str],
    report_overview: dict[str, Any] | None,
    meta: dict[str, Any] | None,
    client: LlmClient,
    model: str,
) -> tuple[str, dict[str, dict[str, Any]]]:
    prompt = role_runtime_prompt(role_code)
    weights = prompt.get("weights") or (prompt.get("scoring") or {}).get("weights") or {}
    scoring = prompt.get("scoring") or {}
    cap = scoring.get("info_insufficient_cap", 65)
    prefer_tools = (prompt.get("mcp") or {}).get("prefer_tools") or []
    mcp_evidence = await collect_mcp_evidence(prefer_tools, meta)

    weight_hint = ", ".join(f"{k}{int(v)}" for k, v in weights.items())
    subscore_keys = ", ".join(weights.keys()) if weights else "按角色权重键"
    evaluate_mode = (meta or {}).get("mode") == "evaluate"
    review_mode = "library_optimize" if evaluate_mode else "incremental"
    user = _fill_template(
        prompt.get("user_template", ""),
        {
            "mode": review_mode,
            "factor_ids_to_review": factor_ids,
            "market": (meta or {}).get("market", ""),
            "date_range_hint": (meta or {}).get("date_range_hint", ""),
            "report_overview": report_overview or {},
            "factors": targets,
        },
    )
    evaluate_hint = (
        "\n\n# 输入模式：因子库优化评估\n"
        "待评对象来自因子库公式种子或历史任务产出，不是研报摘录。"
        "请按公式可实现性、口径完整性、稳健性与可交易性评分；"
        "不要因缺少「研报原文引用」而一票否决或无故压分；"
        "suggestions 应给出可落地的公式/口径改进，而非要求回到研报找证据。"
        if evaluate_mode
        else ""
    )
    anchors_block = _format_score_anchors(scoring)
    system = (
        prompt["system"]
        + evaluate_hint
        + (f"\n\n{anchors_block}" if anchors_block else "")
        + f"\n\n# 本轮硬性输出约束\n"
        + f"- 子项权重（subscores 必须打齐这些键）：{weight_hint or '无'}。\n"
        + f"- subscores 键名必须为：{subscore_keys}。\n"
        + "- 不要自行计算或输出依赖的 total_score（可省略）；服务端按权重加权。\n"
        + "- 每个 listed factor_id 都必须出现在 reviews 中。\n"
        + "- comment：至少 2 句，点名通过/未通过的检查项，禁止空泛「整体尚可」。\n"
        + "- suggestions：字符串数组；综合偏弱或 info_insufficient 时至少 1 条可执行建议。\n"
        + "- 隔离：禁止提及 R1–R6 其他角色或其可能观点。\n"
        + "- JSON only；comment/suggestions 如需引号用中文「」；禁止尾逗号与 markdown。\n"
        + "请输出 JSON：{role_code, role_name, reviews:[{factor_id, subscores:{...}, comment, suggestions, veto, veto_reason, info_insufficient}]}。"
        + f"\n可选 MCP 观察（勿编造）：{json.dumps(mcp_evidence, ensure_ascii=False)}"
    )
    import time

    from factor_backend.services import metrics

    t0 = time.perf_counter()
    try:
        async def _once() -> Any:
            return await client.chat_json(system=system, user=user, model=model)

        raw = await retry_async(
            _once,
            attempts=max(1, int(client.cfg.max_retries or 2) + 1),
            label=f"{role_code} 评审",
            retryable=is_retryable_llm_error,
        )
        metrics.incr("llm_review_calls_total")
        metrics.observe_ms("llm_review_latency", (time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001
        metrics.incr("llm_review_errors_total")
        metrics.observe_ms("llm_review_latency", (time.perf_counter() - t0) * 1000)
        raise LlmError(f"{role_code} 评审调用失败: {e}") from e
    reviews = raw.get("reviews") if isinstance(raw, dict) else None
    if not isinstance(reviews, list):
        raise LlmError(f"{role_code} 返回格式无效（缺少 reviews 数组）")

    by_id = {r.get("factor_id"): r for r in reviews if isinstance(r, dict)}
    out: dict[str, dict[str, Any]] = {}
    for f in targets:
        fid = f["factor_id"]
        item = by_id.get(fid) or {
            "factor_id": fid,
            "subscores": {},
            "total_score": 50,
            "comment": "模型未返回该因子，记为中性分",
            "suggestions": ["请补全评审"],
            "veto": False,
            "veto_reason": None,
            "info_insufficient": True,
        }
        subs = ensure_subscores(item, weights, fallback_total=item.get("total_score"))
        total = weighted_total(
            subs,
            weights,
            info_insufficient=bool(item.get("info_insufficient")),
            info_insufficient_cap=float(cap or 65),
        )
        out[fid] = {
            "role_code": role_code,
            "role_name": prompt.get("name") or role_code,
            "factor_id": fid,
            "subscores": subs,
            "weights": weights,
            "total_score": total,
            "comment": item.get("comment") or "",
            "suggestions": item.get("suggestions") or [],
            "veto": bool(item.get("veto")),
            "veto_reason": item.get("veto_reason"),
            "mcp_evidence": mcp_evidence,
            "data_unavailable": any(e.get("data_unavailable") for e in mcp_evidence),
        }
    return role_code, out


async def _review_one_role_mock(
    *,
    role_code: str,
    role_name: str,
    targets: list[dict[str, Any]],
    meta: dict[str, Any] | None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    prompt = role_runtime_prompt(role_code)
    weights = prompt.get("weights") or (prompt.get("scoring") or {}).get("weights") or {}
    prefer_tools = (prompt.get("mcp") or {}).get("prefer_tools") or []
    mcp_evidence = await collect_mcp_evidence(prefer_tools, meta)
    out: dict[str, dict[str, Any]] = {}
    for f in targets:
        base = richness_score(f)
        # 稳定微调
        adj = (sum(ord(c) for c in role_code + f.get("factor_id", "")) % 7) - 3
        if f.get("status") == "REFINED":
            adj += 8
        seed = max(0.0, min(100.0, base + adj))
        subs = {k: max(0.0, min(100.0, seed + ((i % 5) - 2))) for i, k in enumerate(weights.keys() or ["Logic"])}
        if not weights:
            weights = {"Logic": 100}
            subs = {"Logic": seed}
        total = weighted_total(subs, weights)
        out[f["factor_id"]] = {
            "role_code": role_code,
            "role_name": role_name,
            "factor_id": f["factor_id"],
            "subscores": subs,
            "weights": weights,
            "total_score": total,
            "comment": f"{role_name}加权评分（mock）完整度约 {base:.0f}",
            "suggestions": ["补全中性化/字段口径"] if total < 85 else [],
            "veto": not bool((f.get("definition") or {}).get("formula_or_rule")),
            "veto_reason": "缺少公式" if not (f.get("definition") or {}).get("formula_or_rule") else None,
            "mcp_evidence": mcp_evidence,
            "data_unavailable": any(e.get("data_unavailable") for e in mcp_evidence),
        }
    return role_code, out


async def run_role_reviews(
    *,
    factors: list[dict[str, Any]],
    factor_ids: list[str],
    report_overview: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    cfg: LlmRuntimeConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """六角色并行 gather（受 review_concurrency 限制）；服务端按提示词权重重算 total_score。"""
    from factor_backend.config import get_settings

    cfg = cfg or get_llm_config()
    targets = [f for f in factors if f.get("factor_id") in set(factor_ids)]
    scorecards: dict[str, dict[str, Any]] = {f["factor_id"]: {} for f in targets}
    concurrency = max(1, int(get_settings().review_concurrency))
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(coro):
        async with sem:
            return await coro

    if not cfg.should_call_llm:
        if not (cfg.use_mock or not cfg.enabled):
            raise LlmError("LLM 未就绪：无法评审")
        tasks = [
            _guarded(_review_one_role_mock(role_code=code, role_name=name, targets=targets, meta=meta))
            for code, name, _ in ROLES
            if code in ROLE_FILES
        ]
    else:
        client = LlmClient(cfg)
        tasks = [
            _guarded(
                _review_one_role_live(
                    role_code=code,
                    targets=targets,
                    factor_ids=factor_ids,
                    report_overview=report_overview,
                    meta=meta,
                    client=client,
                    model=cfg.model_review,
                )
            )
            for code, name, _ in ROLES
            if code in ROLE_FILES
        ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures: list[str] = []
    for item in results:
        if isinstance(item, Exception):
            failures.append(str(item))
            continue
        role_code, role_map = item
        for fid, review in role_map.items():
            scorecards.setdefault(fid, {})[role_code] = review
    if failures:
        # 任一角色失败则整轮失败，但错误信息带上角色上下文
        raise LlmError("Step2 评审失败: " + " | ".join(failures[:3]))
    return scorecards
