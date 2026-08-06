from __future__ import annotations

import logging
from typing import Any

from factor_backend.models.schemas import JobProgress, StepType
from factor_backend.services.agents import run_role_reviews, run_step1_extract
from factor_backend.services.factor_library import (
    upsert_job_factors_to_dropped,
    upsert_job_factors_to_workspace,
)
from factor_backend.services.llm_config import get_llm_config
from factor_backend.services.reviewers import ROLES
from factor_backend.services.router_logic import decide_action, merge_scorecards
from factor_backend.services.step_recorder import StepRecorder
from factor_backend.services.storage import get_storage
from factor_backend.graph.state import GraphState

logger = logging.getLogger(__name__)


def _recorder(state: GraphState) -> StepRecorder:
    return StepRecorder(get_storage(), state["job_id"])


def _ensure_not_cancelled(state: GraphState) -> None:
    job_id = state["job_id"]
    if get_storage().is_cancel_requested(job_id):
        get_storage().mark_cancelled(job_id, "cancelled during graph")
        raise RuntimeError("job cancelled")


def _progress(job_id: str, *, phase: str, round_idx: int, message: str, percent: int, **extra: Any) -> None:
    get_storage().update_job(
        job_id,
        progress=JobProgress(phase=phase, round=round_idx, message=message, percent=percent).model_dump(),
        **extra,
    )


def node_ingest(state: GraphState) -> dict[str, Any]:
    _ensure_not_cancelled(state)
    job_id = state["job_id"]
    report_id = state["report_id"]
    storage = get_storage()
    report = storage.get_report_content(report_id)
    report_meta = storage.get_report_meta(report_id)

    _progress(job_id, phase="ingest", round_idx=0, message="读取研报", percent=5)
    _recorder(state).record(
        StepType.ingest,
        title="研报入库",
        summary=f"加载研报 {report_id}，长度 {len(report)} 字符",
        payload={"report_id": report_id, "title": report_meta.get("title"), "chars": len(report), "engine": "langgraph"},
    )
    return {
        "report": report,
        "report_title": report_meta.get("title") or "",
        "round": 0,
        "force_end": False,
        "revise_packet": None,
        "frozen": {},
        "prev_scorecards": {},
        "dropped": [],
        "errors": [],
        "engine": "langgraph",
    }


def _as_factor_id(value: Any) -> str | None:
    """LLM 偶发把 factor_id / changed_ids 写成对象，统一成可哈希字符串。"""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for k in ("factor_id", "id", "name"):
            if k in value and value[k] is not None and not isinstance(value[k], (dict, list)):
                return str(value[k]).strip() or None
        return None
    return str(value).strip() or None


def _normalize_factors(raw_factors: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw_factors, list):
        return out
    for f in raw_factors:
        if not isinstance(f, dict):
            continue
        fid = _as_factor_id(f.get("factor_id"))
        if not fid:
            continue
        item = dict(f)
        item["factor_id"] = fid
        out.append(item)
    return out


def _normalize_id_list(raw_ids: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(raw_ids, list):
        return out
    for x in raw_ids:
        fid = _as_factor_id(x)
        if fid and fid not in out:
            out.append(fid)
    return out


def _seed_factors_from_meta(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """把因子库种子映射为流水线内部 factor 结构。"""
    seeds = meta.get("seed_factors") or []
    out: list[dict[str, Any]] = []
    for raw in seeds:
        if not isinstance(raw, dict) or not raw.get("factor_id"):
            continue
        formula = raw.get("formula_or_rule") or raw.get("formula") or ""
        inputs = raw.get("inputs") or []
        if not isinstance(inputs, list):
            inputs = []
        out.append(
            {
                "factor_id": str(raw["factor_id"]),
                "name_zh": raw.get("name_zh") or raw.get("name") or str(raw["factor_id"]),
                "name_en": raw.get("name_en"),
                "category": raw.get("category") or "优化因子",
                "explicit_or_implicit": "显式",
                "status": "NEW",
                "economic_logic": raw.get("economic_logic") or "来自因子库，待六角色评估与优化",
                "source_quote": raw.get("source") or "因子库种子",
                "definition": {
                    "inputs": [str(x) for x in inputs],
                    "formula_or_rule": str(formula),
                    "processing": "未提及",
                    "frequency": raw.get("frequency") or "未提及",
                },
                "signal_direction": raw.get("signal_direction") or "未提及",
                "portfolio_construction": "未提及",
                "universe": "未提及",
                "holding_turnover": "未提及",
                "risks": "口径缺失、样本偏差、拥挤交易",
                "implementability_1_to_5": 4,
                "priority": "中",
                "change_log": None,
                "evidence_quotes": [],
                "unresolved": [],
                "mcp_evidence": [],
                "data_check": "skipped",
            }
        )
    return out


async def node_step1(state: GraphState) -> dict[str, Any]:
    _ensure_not_cancelled(state)
    job_id = state["job_id"]
    round_idx = int(state.get("round") or 0) + 1
    revise_packet = state.get("revise_packet")
    frozen = dict(state.get("frozen") or {})
    dropped = list(state.get("dropped") or [])
    report = state["report"]
    meta = state.get("meta") or {}
    cfg = get_llm_config()

    _progress(
        job_id,
        phase="step1",
        round_idx=round_idx,
        message="因子提取/修订",
        percent=10 + (round_idx - 1) * 25,
        rounds_used=round_idx,
    )

    if revise_packet:
        _recorder(state).record(
            StepType.revise_loop,
            title=f"第{round_idx}轮回灌修订",
            summary=f"修订因子: {[i.get('factor_id') for i in revise_packet.get('items', [])]}",
            payload=revise_packet,
            round=round_idx,
        )

    # evaluate 首轮：跳过 LLM 提取，直接注入种子因子
    if meta.get("mode") == "evaluate" and not revise_packet:
        factors = _seed_factors_from_meta(meta)
        if not factors:
            raise RuntimeError("evaluate 模式缺少 seed_factors")
        factor_map = {f["factor_id"]: f for f in factors}
        for fid, fobj in frozen.items():
            factor_map[fid] = {**fobj, "status": "FROZEN"}
        factors = list(factor_map.values())
        changed_ids = [f["factor_id"] for f in factors if f.get("status") != "FROZEN" and f["factor_id"] not in frozen]
        extract = {
            "report_overview": {
                "title": state.get("report_title") or "因子优化评估",
                "one_liner": f"因子库种子评估，共 {len(factors)} 个因子",
                "report_type": "factor_library_evaluate",
            },
            "core_logic": ["对因子库所选因子做六角色评估与优化"],
            "factors": factors,
            "non_factors": [],
            "changed_ids": changed_ids,
            "downgraded_ids": [],
            "data_checks": {"summary": "seed mode, skipped extract"},
        }
        _recorder(state).record(
            StepType.step1_extract,
            title=f"Step1 种子注入（round={round_idx}）",
            summary=f"注入 {len(factors)} 个因子库种子，ChangedIds={changed_ids}",
            payload={
                "overview": extract.get("report_overview"),
                "factors": factors,
                "changed_ids": changed_ids,
                "mode": "seed",
                "prompt_ref": None,
                "llm_mode": "skipped",
                "engine": "langgraph",
            },
            round=round_idx,
        )
        return {
            "round": round_idx,
            "extract": extract,
            "factors": factors,
            "changed_ids": changed_ids,
            "dropped": dropped,
            "force_end": False,
            "new_scorecards": {},
            "gate_rows": [],
            "revise_items": [],
        }

    _progress(
        job_id,
        phase="step1",
        round_idx=round_idx,
        message="等待 LLM 返回（修订/提取）…",
        percent=12 + (round_idx - 1) * 25,
        rounds_used=round_idx,
    )
    extract = await run_step1_extract(
        report=report,
        revise_packet=revise_packet,
        round_idx=round_idx,
        meta=meta,
        frozen_factors=list(frozen.values()),
        prev_factors=state.get("factors") or [],
        cfg=cfg,
    )
    factors = _normalize_factors(extract.get("factors", []))
    factor_map = {f["factor_id"]: f for f in factors}
    # 修订轮 LLM 可能只返回目标因子：保留未改动的上一轮因子，避免列表被掏空
    if revise_packet:
        for prev in state.get("factors") or []:
            if not isinstance(prev, dict):
                continue
            pid = str(prev.get("factor_id") or "")
            if pid and pid not in factor_map and pid not in frozen:
                factor_map[pid] = prev
    for fid, fobj in frozen.items():
        factor_map[str(fid)] = {**fobj, "status": "FROZEN", "factor_id": str(fid)}
    factors = list(factor_map.values())
    extract["factors"] = factors

    target_ids = {
        str(it.get("factor_id"))
        for it in ((revise_packet or {}).get("items") or [])
        if isinstance(it, dict) and it.get("factor_id") is not None
    }
    changed_ids = _normalize_id_list(extract.get("changed_ids")) or [
        f["factor_id"] for f in factors if f.get("status") != "FROZEN"
    ]
    changed_ids = [i for i in changed_ids if i not in frozen]
    if revise_packet and target_ids:
        # 只重评本轮实际修订目标，避免把未改因子又拉进评审拖慢
        changed_ids = [i for i in changed_ids if i in target_ids] or list(target_ids)
    extract["changed_ids"] = changed_ids

    prompt_key = extract.get("_prompt_key") or (
        "step1_optimize" if meta.get("mode") == "evaluate" else "step1_extract"
    )
    if revise_packet:
        step_mode = "optimize_revise" if meta.get("mode") == "evaluate" else "revise"
    else:
        step_mode = "extract"

    _recorder(state).record(
        StepType.step1_extract,
        title=f"Step1 {'优化修订' if step_mode == 'optimize_revise' else '提取/修订'}（round={round_idx}）",
        summary=f"产出 {len(factors)} 个因子，ChangedIds={changed_ids}",
        payload={
            "overview": extract.get("report_overview"),
            "factors": factors,
            "changed_ids": changed_ids,
            "mode": step_mode,
            "prompt_ref": f"prompts/{prompt_key}.json",
            "llm_mode": "live" if cfg.should_call_llm else "mock",
            "engine": "langgraph",
        },
        round=round_idx,
    )

    force_end = False
    if revise_packet and not changed_ids:
        force_end = True
        for item in revise_packet.get("items", []):
            dropped.append(
                {
                    "factor_id": item.get("factor_id"),
                    "reason": "修订无实质改动（防空转），本轮未进入 ChangedIds，淘汰",
                    "round": round_idx,
                }
            )

    return {
        "round": round_idx,
        "extract": extract,
        "factors": factors,
        "changed_ids": changed_ids,
        "dropped": dropped,
        "force_end": force_end,
        "new_scorecards": {},
        "gate_rows": [],
        "revise_items": [],
    }


async def node_review_fanout(state: GraphState) -> dict[str, Any]:
    _ensure_not_cancelled(state)
    job_id = state["job_id"]
    round_idx = int(state["round"])
    factors = state.get("factors") or []
    frozen = state.get("frozen") or {}
    revise_packet = state.get("revise_packet")
    changed_ids = state.get("changed_ids") or []

    _progress(
        job_id,
        phase="review",
        round_idx=round_idx,
        message="六角色并行评审",
        percent=20 + (round_idx - 1) * 25,
    )

    to_review = (
        _normalize_id_list(changed_ids)
        if revise_packet
        else [str(f["factor_id"]) for f in factors if str(f.get("factor_id")) not in frozen]
    )
    cfg = get_llm_config()
    overview = (state.get("extract") or {}).get("report_overview")
    new_cards = await run_role_reviews(
        factors=factors,
        factor_ids=to_review,
        report_overview=overview,
        meta=state.get("meta") or {},
        cfg=cfg,
    )

    for role_code, role_name, _ in ROLES:
        role_payload = {
            fid: new_cards[fid][role_code]
            for fid in new_cards
            if role_code in new_cards[fid]
        }
        _recorder(state).record(
            StepType.step2_review,
            title=f"Step2 {role_name} 评审",
            summary=f"评审 {len(role_payload)} 个因子",
            payload={
                "reviews": role_payload,
                "prompt_ref": f"step2_{role_code.lower()}",
                "engine": "langgraph",
                "llm_mode": "live" if cfg.should_call_llm else "mock",
            },
            round=round_idx,
            role_code=role_code,
        )

    return {"new_scorecards": new_cards}


def node_review_merge(state: GraphState) -> dict[str, Any]:
    round_idx = int(state["round"])
    revise_packet = state.get("revise_packet")
    prev = state.get("prev_scorecards") or {}
    new_cards = state.get("new_scorecards") or {}
    changed_ids = state.get("changed_ids") or []

    if revise_packet:
        scorecards = merge_scorecards(prev, new_cards, changed_ids)
    else:
        scorecards = new_cards

    _recorder(state).record(
        StepType.step2_merge,
        title="Step2 评分合并",
        summary=f"合并后因子数 {len(scorecards)}",
        payload={"factor_ids": list(scorecards.keys()), "incremental": bool(revise_packet), "engine": "langgraph"},
        round=round_idx,
    )
    return {"scorecards": scorecards, "prev_scorecards": scorecards}


def _gaps(roles: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for r, _, _ in ROLES:
        for s in roles[r].get("suggestions") or []:
            if s not in gaps:
                gaps.append(s)
    return gaps[:5] or ["补全口径与可复现细节"]


def _decision_reason(
    *,
    action: str,
    final_score: float,
    median_score: float,
    veto: bool,
    veto_reasons: list[str],
    mean_min: float,
    median_min: float,
    max_round: int,
    round_idx: int,
    gaps: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if action == "SAVE":
        parts.append(
            f"过线保存：均分 {final_score}≥{mean_min}，中位分 {median_score}≥{median_min}，无否决"
        )
        return "；".join(parts)
    if veto:
        vr = "；".join(str(x) for x in (veto_reasons or []) if x) or "未注明原因"
        parts.append(f"一票否决：{vr}")
    if final_score < mean_min:
        parts.append(f"均分 {final_score}<{mean_min}")
    if median_score < median_min:
        parts.append(f"中位分 {median_score}<{median_min}")
    if action == "REVISE":
        parts.append(f"第 {round_idx}/{max_round} 轮回修订")
        if gaps:
            parts.append("主要缺口：" + "；".join(gaps[:3]))
    elif action == "DROP":
        parts.append(f"未过线且已达 max_round={max_round}，淘汰")
        if gaps:
            parts.append("主要缺口：" + "；".join(gaps[:3]))
    return "；".join(parts) if parts else action


def node_code_gate(state: GraphState) -> dict[str, Any]:
    """确定性门槛：禁止 LLM 心算。"""
    _ensure_not_cancelled(state)
    job_id = state["job_id"]
    round_idx = int(state["round"])
    max_round = int(state["max_round"])
    mean_min = float(state["mean_min"])
    median_min = float(state["median_min"])
    factors = state.get("factors") or []
    scorecards = state.get("scorecards") or {}
    frozen = dict(state.get("frozen") or {})
    dropped = list(state.get("dropped") or [])

    _progress(
        job_id,
        phase="gate",
        round_idx=round_idx,
        message="门槛裁决",
        percent=35 + (round_idx - 1) * 25,
    )

    revise_items: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []

    for fid, roles in scorecards.items():
        if fid in frozen:
            continue
        scores = [float(roles[r]["total_score"]) for r, _, _ in ROLES]
        veto = any(bool(roles[r].get("veto")) for r, _, _ in ROLES)
        veto_reasons = [roles[r].get("veto_reason") for r, _, _ in ROLES if roles[r].get("veto")]
        decision = decide_action(
            scores,
            veto,
            round_idx=round_idx,
            max_round=max_round,
            mean_min=mean_min,
            median_min=median_min,
        )
        row = {
            "factor_id": fid,
            "scores": {r: float(roles[r]["total_score"]) for r, _, _ in ROLES},
            "veto": veto,
            "veto_reasons": [x for x in veto_reasons if x],
            **decision,
        }
        gaps = _gaps(roles)
        row["main_gaps"] = gaps
        row["reason"] = _decision_reason(
            action=str(decision["action"]),
            final_score=float(decision["final_score"]),
            median_score=float(decision["median_score"]),
            veto=veto,
            veto_reasons=[x for x in veto_reasons if x],
            mean_min=mean_min,
            median_min=median_min,
            max_round=max_round,
            round_idx=round_idx,
            gaps=gaps,
        )
        gate_rows.append(row)

        if decision["action"] == "SAVE":
            fobj = next(f for f in factors if f["factor_id"] == fid)
            frozen[fid] = {
                **fobj,
                "final_score": decision["final_score"],
                "median_score": decision["median_score"],
                "scores": row["scores"],
                "save_reason": row["reason"],
            }
        elif decision["action"] == "REVISE":
            revise_items.append(
                {
                    "factor_id": fid,
                    "final_score": decision["final_score"],
                    "median_score": decision["median_score"],
                    "main_gaps": gaps,
                    "reason": row["reason"],
                    "role_feedback": [
                        {
                            "role": r,
                            "score": roles[r]["total_score"],
                            "comment": roles[r].get("comment"),
                            "suggestions": roles[r].get("suggestions", []),
                            "veto": roles[r].get("veto", False),
                        }
                        for r, _, _ in ROLES
                    ],
                    "revise_goals": [
                        "回到研报原文定位对应段落",
                        "补全计算公式与字段",
                        "区分推断与原文",
                    ],
                }
            )
        else:
            dropped.append(
                {
                    "factor_id": fid,
                    "reason": row["reason"],
                    "final_score": decision["final_score"],
                    "median_score": decision["median_score"],
                    "scores": row["scores"],
                    "veto": veto,
                    "veto_reasons": row["veto_reasons"],
                    "main_gaps": gaps,
                    "round": round_idx,
                }
            )

    _recorder(state).record(
        StepType.step3_gate,
        title="Step3 门槛裁决",
        summary=(
            f"SAVE={sum(1 for r in gate_rows if r['action']=='SAVE')} "
            f"REVISE={sum(1 for r in gate_rows if r['action']=='REVISE')} "
            f"DROP={sum(1 for r in gate_rows if r['action']=='DROP')}"
        ),
        payload={
            "rules": {"mean_min": mean_min, "median_min": median_min, "require_no_veto": True},
            "rows": gate_rows,
            "engine": "langgraph",
        },
        round=round_idx,
    )

    if revise_items and round_idx < max_round:
        # 每轮最多修订 N 个最低分因子，避免一次塞太多导致 LLM 长时间挂起看起来像卡死
        revise_cap = 5
        revise_items_sorted = sorted(
            revise_items,
            key=lambda x: (float(x.get("final_score") or 0), float(x.get("median_score") or 0)),
        )
        revise_now = revise_items_sorted[:revise_cap]
        deferred = revise_items_sorted[revise_cap:]
        revise_packet = {
            "needed": True,
            "instruction": (
                f"只修订下列 {len(revise_now)} 个低分因子；FROZEN勿改"
                + (f"；另有 {len(deferred)} 个待下轮" if deferred else "")
            ),
            "items": revise_now,
            "deferred_ids": [d.get("factor_id") for d in deferred],
        }
        route = "revise"
    else:
        # remaining REVISE at max_round already converted to DROP inside decide_action
        revise_packet = None
        route = "persist"

    return {
        "frozen": frozen,
        "dropped": dropped,
        "gate_rows": gate_rows,
        "revise_items": revise_items,
        "revise_packet": revise_packet,
        "route": route,
    }


def node_persist(state: GraphState) -> dict[str, Any]:
    job_id = state["job_id"]
    report_id = state["report_id"]
    frozen = state.get("frozen") or {}
    dropped = list(state.get("dropped") or [])
    rounds_used = int(state.get("round") or 0)
    last_extract = state.get("extract") or {}
    factors_by_id = {
        str(f.get("factor_id")): f
        for f in (last_extract.get("factors") or state.get("factors") or [])
        if isinstance(f, dict) and f.get("factor_id") is not None
    }

    saved_factors = []
    for fid, fobj in frozen.items():
        definition = fobj.get("definition")
        if isinstance(definition, dict):
            formula = definition.get("formula_or_rule") or definition.get("formula")
            inputs = definition.get("inputs") or []
            frequency = definition.get("frequency")
            processing = definition.get("processing")
        elif isinstance(definition, str) and definition.strip():
            formula = definition.strip()
            inputs, frequency, processing = [], fobj.get("frequency"), None
        else:
            formula = fobj.get("formula_or_rule") or fobj.get("formula") or fobj.get("calculation")
            inputs, frequency, processing = [], fobj.get("frequency"), None
        save_reason = fobj.get("save_reason") or (
            f"过线保存：均分 {fobj.get('final_score')}，中位分 {fobj.get('median_score')}，无否决"
        )
        saved_factors.append(
            {
                "factor_id": fid,
                "name_zh": fobj.get("name_zh") or fobj.get("name"),
                "name_en": fobj.get("name_en"),
                "category": fobj.get("category"),
                "formula_or_rule": formula,
                "inputs": inputs or [],
                "frequency": frequency,
                "signal_direction": fobj.get("signal_direction"),
                "economic_logic": fobj.get("economic_logic"),
                "final_score": fobj.get("final_score"),
                "median_score": fobj.get("median_score"),
                "scores": fobj.get("scores") or {},
                "status": "SAVE",
                "reason": save_reason,
                "processing": processing,
                "source_quote": fobj.get("source_quote") or fobj.get("origin_text"),
            }
        )

    # 未过线但有公式：写入淘汰库
    dropped_factors: list[dict[str, Any]] = []
    saved_ids = {str(f["factor_id"]) for f in saved_factors}
    for d in dropped:
        if not isinstance(d, dict):
            continue
        fid = str(d.get("factor_id") or "")
        if not fid or fid in saved_ids:
            continue
        src = factors_by_id.get(fid) or {}
        merged = {**src, **{k: v for k, v in d.items() if v is not None}}
        definition = merged.get("definition")
        if isinstance(definition, dict):
            formula = definition.get("formula_or_rule") or definition.get("formula")
            inputs = definition.get("inputs") or []
            frequency = definition.get("frequency")
        elif isinstance(definition, str) and definition.strip():
            formula = definition.strip()
            inputs, frequency = [], merged.get("frequency")
        else:
            formula = merged.get("formula_or_rule") or merged.get("formula") or merged.get("calculation")
            inputs, frequency = merged.get("inputs") or [], merged.get("frequency")
        if not formula:
            continue
        dropped_factors.append(
            {
                "factor_id": fid,
                "name_zh": merged.get("name_zh") or merged.get("name") or fid,
                "name_en": merged.get("name_en"),
                "category": merged.get("category"),
                "formula_or_rule": formula,
                "inputs": inputs or [],
                "frequency": frequency,
                "signal_direction": merged.get("signal_direction"),
                "economic_logic": merged.get("economic_logic"),
                "final_score": d.get("final_score") if d.get("final_score") is not None else merged.get("final_score"),
                "median_score": d.get("median_score")
                if d.get("median_score") is not None
                else merged.get("median_score"),
                "scores": d.get("scores") or merged.get("scores") or {},
                "status": "DROP",
                "reason": d.get("reason") or "门槛淘汰",
            }
        )

    def _brief(items: list[dict[str, Any]], *, limit: int = 4) -> str:
        bits: list[str] = []
        for it in items[:limit]:
            fid = it.get("factor_id")
            reason = (it.get("reason") or "").strip()
            if reason:
                short = reason if len(reason) <= 80 else reason[:77] + "…"
                bits.append(f"{fid}（{short}）")
            else:
                bits.append(str(fid))
        extra = len(items) - limit
        if extra > 0:
            bits.append(f"等{extra}个")
        return "；".join(bits)

    summary_parts = [
        f"保存 {len(saved_factors)} 个因子，淘汰入库 {len(dropped_factors)} 个"
    ]
    if saved_factors:
        summary_parts.append("保存：" + _brief(saved_factors))
    if dropped_factors:
        summary_parts.append("淘汰：" + _brief(dropped_factors))

    result = {
        "job_id": job_id,
        "report_id": report_id,
        "status": "succeeded",
        "rounds_used": rounds_used,
        "factors": saved_factors,
        "candidates": dropped_factors,  # 兼容旧字段：任务详情仍可展示淘汰因子
        "dropped": dropped,
        "extract_final": last_extract,
        "frozen_ids": list(frozen.keys()),
        "engine": "langgraph",
    }

    storage = get_storage()
    storage.save_result(job_id, result)

    library_ok = True
    library_error: str | None = None
    library_workspace_count = 0
    library_dropped_count = 0
    try:
        workspace_pack = upsert_job_factors_to_workspace(job_id=job_id, saved=saved_factors)
        dropped_pack = upsert_job_factors_to_dropped(job_id=job_id, dropped=dropped_factors)
        library_workspace_count = int((workspace_pack or {}).get("count") or 0)
        library_dropped_count = int((dropped_pack or {}).get("count") or 0)
    except Exception as e:  # noqa: BLE001
        library_ok = False
        library_error = f"{type(e).__name__}: {e}"
        from factor_backend.services import metrics

        metrics.incr("factor_library_write_errors_total")
        logger.exception("factor library upsert failed for job %s", job_id)

    if not library_ok:
        summary_parts.append(f"因子库写入失败：{library_error}")

    done_message = "完成" if library_ok else f"完成（因子库写入失败：{library_error}）"
    _recorder(state).record(
        StepType.persist,
        title="结果落盘",
        summary="。".join(summary_parts),
        payload={
            "saved_ids": [f["factor_id"] for f in saved_factors],
            "candidate_ids": [f["factor_id"] for f in dropped_factors],
            "dropped_ids": [d.get("factor_id") for d in dropped],
            "saved": [
                {
                    "factor_id": f["factor_id"],
                    "name_zh": f.get("name_zh"),
                    "category": f.get("category"),
                    "formula_or_rule": f.get("formula_or_rule"),
                    "final_score": f.get("final_score"),
                    "median_score": f.get("median_score"),
                    "reason": f.get("reason"),
                    "action": "SAVE",
                }
                for f in saved_factors
            ],
            "candidates": [
                {
                    "factor_id": f["factor_id"],
                    "name_zh": f.get("name_zh"),
                    "category": f.get("category"),
                    "formula_or_rule": f.get("formula_or_rule"),
                    "final_score": f.get("final_score"),
                    "median_score": f.get("median_score"),
                    "reason": f.get("reason"),
                    "action": "DROP",
                }
                for f in dropped_factors
            ],
            "dropped": [
                {
                    "factor_id": d.get("factor_id"),
                    "final_score": d.get("final_score"),
                    "median_score": d.get("median_score"),
                    "veto": d.get("veto"),
                    "veto_reasons": d.get("veto_reasons") or [],
                    "main_gaps": d.get("main_gaps") or [],
                    "reason": d.get("reason"),
                    "round": d.get("round"),
                    "action": "DROP",
                }
                for d in dropped
                if isinstance(d, dict)
            ],
            "engine": "langgraph",
            "library_packs": ["workspace", "dropped"],
            "library_write_ok": library_ok,
            "library_write_error": library_error,
            "library_workspace_count": library_workspace_count,
            "library_dropped_count": library_dropped_count,
        },
        status="succeeded" if library_ok else "warning",
    )
    storage.update_job(
        job_id,
        status="succeeded",
        rounds_used=rounds_used,
        saved_count=len(saved_factors),
        dropped_count=len(dropped),
        progress=JobProgress(
            phase="done",
            round=rounds_used,
            message=done_message,
            percent=100,
        ).model_dump(),
        # 任务结果已落 DB；因子库失败记入 error 便于 UI/运维发现，不改 succeeded 状态
        error=None if library_ok else f"library_write_failed: {library_error}",
    )
    return {"saved_factors": saved_factors, "result": result}


def route_after_step1(state: GraphState) -> str:
    if state.get("force_end"):
        return "persist"
    return "review"


def route_after_gate(state: GraphState) -> str:
    if state.get("route") == "revise":
        return "revise"
    return "persist"
