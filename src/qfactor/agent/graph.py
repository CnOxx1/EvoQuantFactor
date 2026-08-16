from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from qfactor.agent.checkpoint import CheckpointStore
from qfactor.agent.coldstart import cold_start_cfg, ensure_dsl_seeds, is_cold_start
from qfactor.agent.experiments import ExperimentLedger, require_discovery_contract
from qfactor.agent.diversity import (
    expression_fingerprint,
    is_banned_expression,
    keep_mechanism_coverage,
    library_diversity_index,
    merge_bans,
    record_lesson,
    active_skeleton_bans,
    keep_skeleton_counts,
    saturated_keep_skeletons,
)
from qfactor.agent.generator import (
    CandidateGenerator,
    _production_llm_cfg,
    should_expand_compose_catalog,
)
from qfactor.agent.llm import LLMClient
from qfactor.agent.reviewer import CandidateReviewer
from qfactor.eval.service import EvalService
from qfactor.factor.base import FactorSpec
from qfactor.factor.cohort import apply_parent_eligibility
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


def _dsl_factor_code(name: str, expression: str, mechanism: str, hypothesis: str) -> str:
    """Render trusted loader code using Python literals, never raw LLM text.

    Expressions are DSL-validated before this point, but the LLM-provided hypothesis
    remains free text.  Serializing every dynamic value with ``repr`` prevents quotes
    or newlines in metadata from escaping into executable source.
    """
    name_lit = repr(name)
    expression_lit = repr(expression)
    mechanism_lit = repr(mechanism)
    hypothesis_lit = repr(hypothesis)
    return f'''from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name={name_lit},
            version="0.1.0",
            status="draft",
            family="price_volume",
            category={mechanism_lit},
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={{"expression": {expression_lit}}},
            tags=["dsl", "loop"],
            hypothesis={hypothesis_lit},
            entry_gate="research",
            expression={expression_lit},
            mechanism={mechanism_lit},
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
'''


class ProductionState(TypedDict, total=False):
    """Serializable LangGraph state for factor production."""

    run_id: str
    run_dir: str
    experiment_id: str
    max_rounds: int
    rounds_done: int
    batch_size: int
    theme: str | None
    round_theme: str | None
    gate_name: str
    llm_ratio: float
    llm_review_ratio: float
    llm_spotcheck_every: int | None
    tested_hashes: list[str]
    saved_factors: list[str]
    mechanism_hits: dict[str, int]
    history: list[dict[str, Any]]
    produced: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    round_stats: dict[str, Any]
    orchestrator: str
    lessons: list[dict[str, Any]]
    banned_skeletons: list[str]
    banned_hashes: list[str]
    high_corr_skeletons: list[str]
    recent_themes: list[str]
    cold_start: bool
    last_catalog_expand_round: int | None
    clean_experiment: bool


class ProductionContext:
    """Non-serializable services closed over by graph nodes."""

    def __init__(self, cfg: ProjectConfig | None = None, llm: LLMClient | None = None):
        self.cfg = cfg or get_project_config()
        self.llm = llm or LLMClient()
        self.registry = FactorRegistry(self.cfg)
        self.eval = EvalService(self.cfg)
        self.generator = CandidateGenerator(self.cfg, self.llm)
        self.reviewer = CandidateReviewer(self.llm)
        self.checkpoint = CheckpointStore("loop_csi100", self.cfg)
        self.experiment: ExperimentLedger | None = None


def _current_data_version(ctx: ProductionContext) -> str:
    try:
        return str(ctx.eval.data.data_version() or "")
    except Exception:
        return ""


def _eligible_research_library(
    ctx: ProductionContext, state: ProductionState
) -> list[dict[str, Any]]:
    """Parents are same-version eligible factors, not one experiment's 4-trial slice.

    ``clean_experiment`` still excludes snapshot / unverified / other-panel rows
    via ``apply_parent_eligibility``. It must not zero the parent book every
    time a factory cycle opens a new experiment_id.
    """
    del state  # eligibility is panel + cohort, not the current ledger id
    current_dv = _current_data_version(ctx)
    rows: list[dict[str, Any]] = []
    for row in ctx.registry.existing_summaries():
        meta = apply_parent_eligibility(row, current_dv)
        if not meta.get("parent_eligible"):
            continue
        merged = dict(row)
        merged.update(meta)
        rows.append(merged)
    return rows


def _node_decide(ctx: ProductionContext):
    def decide(state: ProductionState) -> dict[str, Any]:
        existing = _eligible_research_library(ctx, state)
        coverage = keep_mechanism_coverage(existing)
        lessons = list(state.get("lessons") or [])
        recent_themes = list(state.get("recent_themes") or [])
        forced = state.get("theme")
        cold = is_cold_start(
            existing, ctx.cfg, current_data_version=_current_data_version(ctx)
        )
        disable_fsa = bool(cold_start_cfg(ctx.cfg)["disable_fsa"] and cold)
        div_cfg = (ctx.cfg.project.get("production") or {}).get("diversity") or {}
        max_per_skel = int(div_cfg.get("max_per_skeleton", 2))
        index = (
            ctx.generator.diversity_index(existing, max_per_skel)
            if state.get("clean_experiment")
            else library_diversity_index(ctx.cfg)
        )
        keep_sat = saturated_keep_skeletons(existing, max_per_skel)
        extra_hc = list(state.get("high_corr_skeletons") or [])
        banned_skels = (
            sorted(set(index.get("banned_skeletons") or []) | keep_sat | set(extra_hc))
            if state.get("clean_experiment")
            else sorted(
                active_skeleton_bans(
                    ctx.cfg,
                    extra=extra_hc,
                    cold_start=disable_fsa,
                    existing=existing,
                )
            )
        )
        banned_hashes = sorted(
            set(index.get("expr_hashes") or []) | set(state.get("banned_hashes") or [])
        )
        round_theme = ctx.generator.decide_theme(
            coverage,
            existing,
            lessons=lessons,
            forced_theme=forced,
            recent_themes=recent_themes,
        )
        if round_theme:
            recent_themes = (recent_themes + [round_theme])[-12:]
        return {
            "round_theme": round_theme,
            "recent_themes": recent_themes,
            "rounds_done": int(state.get("rounds_done") or 0) + 1,
            "candidates": [],
            "round_stats": {},
            "banned_skeletons": banned_skels,
            "banned_hashes": banned_hashes,
            "cold_start": cold,
        }

    return decide


def _node_generate(ctx: ProductionContext):
    def generate(state: ProductionState) -> dict[str, Any]:
        existing = _eligible_research_library(ctx, state)
        coverage = keep_mechanism_coverage(existing)
        lessons = list(state.get("lessons") or [])
        ledger = getattr(ctx, "experiment", None)
        if ledger is None and state.get("experiment_id"):
            raise RuntimeError("Discovery experiment ledger is required before candidate generation")
        requested = int(state.get("batch_size") or 8)
        remaining = (ledger.max_trials - ledger.trial_count) if ledger is not None else requested
        if remaining <= 0:
            raise RuntimeError(
                f"Experiment {ledger.experiment_id} exhausted its trial budget ({ledger.max_trials})"
            )
        cands = ctx.generator.generate_batch(
            n=min(requested, remaining),
            theme=state.get("round_theme"),
            coverage=coverage,
            existing=existing,
            llm_ratio=float(state["llm_ratio"]) if state.get("llm_ratio") is not None else float(
                ctx.generator.llm_cfg.get("llm_ratio", 0.45)
            ),
            lessons=lessons,
            extra_banned_skeletons=list(state.get("banned_skeletons") or []),
            extra_banned_hashes=list(state.get("banned_hashes") or []),
            round_idx=int(state.get("rounds_done") or 0),
            clean_experiment=bool(state.get("clean_experiment")),
        )
        src_counts: dict[str, int] = {}
        for ordinal, c in enumerate(cands, 1):
            src = str(c.get("source", "unknown"))
            src_counts[src] = src_counts.get(src, 0) + 1
            c["research_cohort"] = (
                "clean_discovery" if state.get("clean_experiment") else "current_discovery"
            )
            if ledger is not None:
                trial_id = f"{ledger.experiment_id}:r{int(state.get('rounds_done') or 0)}:{ordinal}"
                c["_trial_id"] = trial_id
                ledger.record_trial(
                    trial_id=trial_id,
                    stage="generated",
                    outcome="generated",
                    candidate=c,
                    detail={"round": int(state.get("rounds_done") or 0), "theme": state.get("round_theme")},
                )
        gen_stats = dict(getattr(ctx.generator, "last_stats", None) or {})
        round_stats = {
            "iteration": int(state.get("rounds_done") or 0),
            "theme": state.get("round_theme"),
            "generated": len(cands),
            "experiment_id": ledger.experiment_id if ledger is not None else None,
            "trial_count": ledger.trial_count if ledger is not None else 0,
            "trial_budget": ledger.max_trials if ledger is not None else None,
            "sources": src_counts,
            "llm_ratio": state.get("llm_ratio"),
            "llm_fresh_ok": gen_stats.get("llm_fresh_ok"),
            "llm_fresh_empty": gen_stats.get("llm_fresh_empty"),
            "llm_mutate_ok": gen_stats.get("llm_mutate_ok"),
            "llm_mutate_empty": gen_stats.get("llm_mutate_empty"),
            "llm_compile_empty": gen_stats.get("llm_compile_empty"),
            "llm_errors": gen_stats.get("llm_errors"),
            "hint_fallback": gen_stats.get("hint_fallback"),
            "compose_fallback": gen_stats.get("compose_fallback"),
            "structure_perturb": gen_stats.get("structure_perturb"),
            "llm_skipped": gen_stats.get("llm_skipped"),
            "force_library_mutate": gen_stats.get("force_library_mutate"),
            "n_mutate": gen_stats.get("n_mutate"),
            "n_crossover": gen_stats.get("n_crossover"),
            "n_usable": gen_stats.get("n_usable"),
            "llm_ideas": gen_stats.get("llm_ideas"),
            "unused_compose": gen_stats.get("unused_compose"),
            "cold_start": gen_stats.get("cold_start"),
            "n_fresh": gen_stats.get("n_fresh"),
            "crossover_ok": gen_stats.get("crossover_ok"),
            "blocked_mechanisms": gen_stats.get("blocked_mechanisms"),
            "curriculum": gen_stats.get("curriculum"),
            "keep_coverage": gen_stats.get("keep_coverage"),
            "prior_refreshed": gen_stats.get("prior_refreshed"),
            "catalog_expand": None,
            "reviewed_ok": 0,
            "saved": [],
            "rejected": 0,
            "diversity_rejects": 0,
        }
        return {"candidates": cands, "round_stats": round_stats}

    return generate


def _node_review_validate(ctx: ProductionContext):
    def review_validate(state: ProductionState) -> dict[str, Any]:
        tested = set(state.get("tested_hashes") or [])
        saved = list(state.get("saved_factors") or [])
        coverage = dict(state.get("mechanism_hits") or {})
        produced = list(state.get("produced") or [])
        lessons = list(state.get("lessons") or [])
        banned_skels = set(state.get("banned_skeletons") or [])
        banned_hashes = set(state.get("banned_hashes") or [])
        high_corr_skels = set(state.get("high_corr_skeletons") or [])
        bans = merge_bans(
            {"expr_hashes": banned_hashes, "banned_skeletons": banned_skels},
        )
        round_stats = dict(state.get("round_stats") or {})
        cands = list(state.get("candidates") or [])
        gate_name = str(state.get("gate_name") or "research")
        ledger = getattr(ctx, "experiment", None)
        if ledger is None and state.get("experiment_id"):
            raise RuntimeError("Discovery experiment ledger is required before review")
        llm_review_ratio = float(state.get("llm_review_ratio") or 0)
        every = state.get("llm_spotcheck_every")
        use_every = every is not None and int(every) > 0
        div_cfg = (ctx.cfg.project.get("production") or {}).get("diversity") or {}
        corr_ban = float(div_cfg.get("max_corr_ban", 0.95))
        max_per_skel = int(div_cfg.get("max_per_skeleton", 2))
        keep_counts = keep_skeleton_counts(_eligible_research_library(ctx, state))
        round_eval_skels: set[str] = set()
        cold = bool(state.get("cold_start"))
        cheap_min = float(cold_start_cfg(ctx.cfg)["cheap_ic_min"])

        for i, cand in enumerate(cands):
            # hard diversity gate before review
            banned, why = is_banned_expression(cand["expression"], bans)
            sk = None
            try:
                fp0 = expression_fingerprint(cand["expression"])
                sk = fp0["skeleton"]
                if not banned and sk in round_eval_skels:
                    banned, why = True, "same_batch_skeleton"
            except Exception:
                pass
            if banned:
                round_stats["diversity_rejects"] = int(round_stats.get("diversity_rejects") or 0) + 1
                round_stats["rejected"] = int(round_stats.get("rejected") or 0) + 1
                if ledger is not None:
                    ledger.record_trial(
                        trial_id=str(cand.get("_trial_id") or cand.get("name")),
                        stage="diversity",
                        outcome="rejected",
                        candidate=cand,
                        detail={"reason": why},
                    )
                lessons = record_lesson(
                    lessons,
                    mechanism=str(cand.get("mechanism") or "unknown"),
                    reason="banned_skeleton" if "skeleton" in why else "duplicate_expr",
                    expression=cand.get("expression"),
                    detail={"why": why},
                )
                try:
                    fp = expression_fingerprint(cand["expression"])
                    tested.add(fp["expr_hash"])
                    banned_hashes.add(fp["expr_hash"])
                except Exception:
                    pass
                continue

            if use_every:
                spot = i % int(every) == 0  # type: ignore[arg-type]
            else:
                spot = random.random() < llm_review_ratio
            rev = ctx.reviewer.review(cand, tested, llm_spotcheck=spot)
            if not rev["ok"]:
                tested.add(str(rev.get("hash") or cand["expression"]))
                round_stats["rejected"] = int(round_stats.get("rejected") or 0) + 1
                if ledger is not None:
                    ledger.record_trial(
                        trial_id=str(cand.get("_trial_id") or cand.get("name")),
                        stage="review",
                        outcome="rejected",
                        candidate=cand,
                        detail={"errors": rev.get("errors")},
                    )
                lessons = record_lesson(
                    lessons,
                    mechanism=str(cand.get("mechanism") or "unknown"),
                    reason="review_reject",
                    expression=cand.get("expression"),
                    detail={"errors": rev.get("errors")},
                )
                continue
            round_stats["reviewed_ok"] = int(round_stats.get("reviewed_ok") or 0) + 1
            h = str(rev["hash"])
            tested.add(h)
            banned_hashes.add(h)
            bans.setdefault("hashes", set()).add(h)
            if sk:
                round_eval_skels.add(sk)

            name = cand["name"]
            if cold and cheap_min > 0:
                try:
                    ic_abs = ctx.eval.quick_rank_ic(cand["expression"])
                except Exception:
                    ic_abs = None
                if ic_abs is not None and ic_abs < cheap_min:
                    round_stats["rejected"] = int(round_stats.get("rejected") or 0) + 1
                    round_stats["cheap_rejects"] = int(round_stats.get("cheap_rejects") or 0) + 1
                    if ledger is not None:
                        ledger.record_trial(
                            trial_id=str(cand.get("_trial_id") or cand.get("name")),
                            stage="cheap_ic",
                            outcome="rejected",
                            candidate=cand,
                            detail={"rank_ic_mean": ic_abs, "threshold": cheap_min},
                        )
                    lessons = record_lesson(
                        lessons,
                        mechanism=str(cand.get("mechanism") or "unknown"),
                        reason="weak_ic",
                        expression=cand.get("expression"),
                        detail={"rank_ic_mean": ic_abs, "cheap": True},
                    )
                    continue
            try:
                report = ctx.eval.evaluate_dsl(
                    cand["expression"], name, gate_name=gate_name
                )
                if ledger is not None:
                    from qfactor.eval.multiple_testing import (
                        research_selection_bias_preview,
                    )

                    preview = research_selection_bias_preview(
                        report.get("metrics") or {},
                        n_trials=ledger.trial_count,
                    )
                    report["research_selection_bias_preview"] = preview
                    report.setdefault("summary", {})[
                        "selection_bias_preview_state"
                    ] = preview["state"]
                status = report.get("gate", {}).get("status", "reject")
            except Exception as e:
                report = {"error": str(e)}
                status = "reject"

            item = {
                "name": name,
                "expression": cand["expression"],
                "mechanism": cand.get("mechanism"),
                "status": status,
                "summary": report.get("summary", {}),
            }
            if status in {"screened", "candidate", "approved"}:
                code = _dsl_factor_code(
                    name=name,
                    expression=cand["expression"],
                    mechanism=cand.get("mechanism", "unknown"),
                    hypothesis=cand.get("hypothesis", ""),
                )
                spec = FactorSpec(
                    name=name,
                    version="0.1.0",
                    status=status,
                    family="price_volume",
                    category=cand.get("mechanism", "unknown"),
                    required_fields=["close", "open", "high", "low"],
                    lookback=int(cand.get("lookback", 20)),
                    tags=[
                        "dsl",
                        "loop",
                        "langgraph",
                        cand.get("source", "gen"),
                        f"experiment:{state.get('experiment_id')}",
                    ],
                    params={
                        "experiment_id": state.get("experiment_id"),
                        "research_cohort": (
                            "clean_discovery"
                            if state.get("clean_experiment")
                            else "current_discovery"
                        ),
                    },
                    hypothesis=cand.get("hypothesis", ""),
                    entry_gate=gate_name,
                    expression=cand["expression"],
                    mechanism=cand.get("mechanism"),
                    expr_hash=h,
                )
                ctx.registry.save_factor_files(
                    spec, code, source=cand.get("source", "loop"), report=report
                )
                saved.append(name)
                round_stats.setdefault("saved", []).append(name)
                if ledger is not None:
                    ledger.record_trial(
                        trial_id=str(cand.get("_trial_id") or name),
                        stage="research_gate",
                        outcome="screened",
                        candidate=cand,
                        detail={"status": status, "summary": report.get("summary") or {}},
                    )
                produced.append(item)
                mid = cand.get("mechanism", "unknown")
                coverage[mid] = coverage.get(mid, 0) + 1
                try:
                    sk_keep = expression_fingerprint(cand["expression"])["skeleton"]
                    keep_counts[sk_keep] = keep_counts.get(sk_keep, 0) + 1
                    if keep_counts[sk_keep] >= max_per_skel:
                        banned_skels.add(sk_keep)
                        bans.setdefault("skeletons", set()).add(sk_keep)
                except Exception:
                    pass
            else:
                round_stats["rejected"] = int(round_stats.get("rejected") or 0) + 1
                if ledger is not None:
                    ledger.record_trial(
                        trial_id=str(cand.get("_trial_id") or name),
                        stage="research_gate",
                        outcome="rejected",
                        candidate=cand,
                        detail={"status": status, "summary": report.get("summary") or {}, "error": report.get("error")},
                    )
                run_dir = Path(str(state.get("run_dir") or ""))
                if run_dir:
                    reject_dir = run_dir / "rejects"
                    reject_dir.mkdir(parents=True, exist_ok=True)
                    (reject_dir / f"{name}.json").write_text(
                        json.dumps(item, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8",
                    )
                summary = report.get("summary") or {}
                metrics = report.get("metrics") or {}
                max_corr = float(summary.get("max_corr") or metrics.get("max_corr") or 0)
                ic = abs(float(summary.get("rank_ic_mean") or metrics.get("rank_ic_mean") or 0))
                reason = "gate_reject"
                if max_corr >= corr_ban:
                    reason = "high_corr"
                    if not cold:
                        try:
                            sk_hc = expression_fingerprint(cand["expression"])["skeleton"]
                            banned_skels.add(sk_hc)
                            high_corr_skels.add(sk_hc)
                            bans.setdefault("skeletons", set()).add(sk_hc)
                        except Exception:
                            pass
                elif ic < 0.01:
                    reason = "weak_ic"
                lessons = record_lesson(
                    lessons,
                    mechanism=str(cand.get("mechanism") or "unknown"),
                    reason=reason,
                    expression=cand.get("expression"),
                    detail={
                        "rank_ic_mean": summary.get("rank_ic_mean"),
                        "max_corr": max_corr,
                        "status": status,
                    },
                )

        return {
            "tested_hashes": sorted(tested)[-5000:],
            "saved_factors": saved,
            "mechanism_hits": coverage,
            "produced": produced,
            "round_stats": round_stats,
            "candidates": [],
            "lessons": lessons[-200:],
            "banned_skeletons": sorted(banned_skels)[-500:],
            "banned_hashes": sorted(banned_hashes)[-5000:],
            "high_corr_skeletons": sorted(high_corr_skels)[-200:],
        }

    return review_validate


def _maybe_expand_catalog(
    ctx: ProductionContext, state: ProductionState, round_stats: dict[str, Any]
) -> tuple[dict[str, Any], int | None]:
    last = state.get("last_catalog_expand_round")
    last_i = int(last) if last is not None else None
    if state.get("clean_experiment"):
        return {"attempted": False, "reason": "clean_experiment"}, last_i
    if round_stats.get("cold_start"):
        return {"attempted": False, "reason": "cold_start"}, last_i
    unused = round_stats.get("unused_compose")
    unused_n = int(unused) if unused is not None else 0
    llm_cfg = ctx.generator.llm_cfg
    due = should_expand_compose_catalog(
        unused_n,
        int(state.get("rounds_done") or 0),
        last_i,
        every=int(llm_cfg.get("catalog_expand_every", 20)),
        unused_lt=int(llm_cfg.get("catalog_expand_unused_lt", 0)),
        empty_every=int(llm_cfg.get("catalog_expand_empty_every", 5)),
    )
    if not due:
        return {"attempted": False, "reason": "not_due"}, last_i
    blocked = set(round_stats.get("blocked_mechanisms") or [])
    try:
        info = ctx.generator.expand_compose_catalog_via_llm(
            max_accept=int(llm_cfg.get("catalog_expand_max", 10)),
            blocked=blocked,
        )
    except Exception as e:
        info = {"attempted": True, "ok": False, "error": str(e), "n_accepted": 0}
    return info, int(state.get("rounds_done") or 0)


def _node_persist(ctx: ProductionContext):
    def persist(state: ProductionState) -> dict[str, Any]:
        history = list(state.get("history") or [])
        round_stats = dict(state.get("round_stats") or {})
        expand_info, last_expand = _maybe_expand_catalog(ctx, state, round_stats)
        round_stats["catalog_expand"] = expand_info
        history.append(round_stats)
        history = history[-200:]
        cp = {
            "iteration": int(state.get("rounds_done") or 0),
            "tested_hashes": list(state.get("tested_hashes") or []),
            "saved_factors": list(state.get("saved_factors") or []),
            "mechanism_hits": dict(state.get("mechanism_hits") or {}),
            "history": history,
            "lessons": list(state.get("lessons") or [])[-200:],
            "banned_skeletons": list(state.get("banned_skeletons") or [])[-500:],
            "banned_hashes": list(state.get("banned_hashes") or [])[-5000:],
            "high_corr_skeletons": list(state.get("high_corr_skeletons") or [])[-200:],
            "recent_themes": list(state.get("recent_themes") or [])[-12:],
            "last_catalog_expand_round": last_expand,
        }
        if not state.get("clean_experiment"):
            ctx.checkpoint.save(cp)
        run_dir = Path(str(state.get("run_dir") or ""))
        if run_dir:
            run_dir.mkdir(parents=True, exist_ok=True)
            iteration = int(state.get("rounds_done") or 0)
            (run_dir / f"round_{iteration}.json").write_text(
                json.dumps(round_stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return {
            "history": history,
            "round_stats": round_stats,
            "last_catalog_expand_round": last_expand,
        }

    return persist


def _should_continue(state: ProductionState) -> Literal["decide", "end"]:
    done = int(state.get("rounds_done") or 0)
    max_rounds = int(state.get("max_rounds") or 1)
    return "decide" if done < max_rounds else "end"


def build_production_graph(ctx: ProductionContext | None = None):
    """
    LangGraph orchestration:

      START -> decide -> generate -> review_validate -> persist
                 ^                                         |
                 +--------------(more rounds)---------------+
                                                     (done) -> END
    """
    ctx = ctx or ProductionContext()
    g: StateGraph = StateGraph(ProductionState)
    g.add_node("decide", _node_decide(ctx))
    g.add_node("generate", _node_generate(ctx))
    g.add_node("review_validate", _node_review_validate(ctx))
    g.add_node("persist", _node_persist(ctx))
    g.add_edge(START, "decide")
    g.add_edge("decide", "generate")
    g.add_edge("generate", "review_validate")
    g.add_edge("review_validate", "persist")
    g.add_conditional_edges(
        "persist",
        _should_continue,
        {"decide": "decide", "end": END},
    )
    return g.compile()


def run_production_graph(
    *,
    cfg: ProjectConfig | None = None,
    llm: LLMClient | None = None,
    rounds: int = 5,
    batch_size: int = 8,
    theme: str | None = None,
    gate_name: str = "research",
    resume: bool = True,
    llm_ratio: float | None = None,
    llm_review_ratio: float | None = None,
    llm_spotcheck_every: int | None = None,
    clean_experiment: bool = False,
) -> dict[str, Any]:
    if gate_name != "research":
        raise RuntimeError(
            "Mining loops are research-only. Run library-reeval-screened and "
            "library-refresh-production for production promotion."
        )
    ctx = ProductionContext(cfg, llm)
    ctx.llm.require_enabled()
    ensure_dsl_seeds(ctx.cfg)

    llm_cfg = _production_llm_cfg(ctx.cfg)
    if llm_ratio is None:
        llm_ratio = float(llm_cfg["llm_ratio"])
    if llm_review_ratio is None:
        llm_review_ratio = float(llm_cfg["llm_review_ratio"])

    if clean_experiment:
        resume = False
    cp = ctx.checkpoint.load() if resume else {
        "iteration": 0,
        "tested_hashes": [],
        "saved_factors": [],
        "mechanism_hits": {},
        "history": [],
        "lessons": [],
        "banned_skeletons": [],
        "banned_hashes": [],
        "high_corr_skeletons": [],
        "recent_themes": [],
        "last_catalog_expand_round": None,
    }

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ctx.cfg.path("runs") / f"loop_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    # No model call occurs before bars and the immutable discovery window exist.
    # PIT/selection evidence remains a separate, binding candidate gate.
    date_partitions = require_discovery_contract(ctx.cfg)
    ctx.experiment = ExperimentLedger(ctx.cfg, run_dir=run_dir / "experiment")
    manifest = ctx.experiment.start(
        run_id=run_id,
        data_version=ctx.eval.data.status().get("data_version"),
        llm=ctx.llm,
        search_config={
            "gate_name": gate_name,
            "rounds": rounds,
            "batch_size": batch_size,
            "theme": theme,
            "llm_ratio": llm_ratio,
            "llm_review_ratio": llm_review_ratio,
            "llm_config": llm_cfg,
            "clean_experiment": bool(clean_experiment),
        },
        date_partitions=date_partitions,
    )
    ctx.eval.clean_experiment = bool(clean_experiment)
    ctx.eval.peer_experiment_id = str(manifest["experiment_id"])

    # Resume continues from checkpoint iteration; `rounds` = additional rounds.
    start_done = int(cp.get("iteration") or 0) if resume else 0

    initial: ProductionState = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "experiment_id": manifest["experiment_id"],
        "max_rounds": start_done + rounds,
        "rounds_done": start_done,
        "batch_size": batch_size,
        "theme": theme,
        "round_theme": None,
        "gate_name": gate_name,
        "llm_ratio": float(llm_ratio),
        "llm_review_ratio": float(llm_review_ratio),
        "llm_spotcheck_every": llm_spotcheck_every,
        "tested_hashes": list(cp.get("tested_hashes") or []),
        "saved_factors": list(cp.get("saved_factors") or []),
        "mechanism_hits": dict(cp.get("mechanism_hits") or {}),
        "history": list(cp.get("history") or []),
        "lessons": list(cp.get("lessons") or []),
        "banned_skeletons": list(cp.get("banned_skeletons") or []),
        "banned_hashes": list(cp.get("banned_hashes") or []),
        "high_corr_skeletons": list(cp.get("high_corr_skeletons") or []),
        "recent_themes": list(cp.get("recent_themes") or []),
        "last_catalog_expand_round": cp.get("last_catalog_expand_round"),
        "produced": [],
        "candidates": [],
        "round_stats": {},
        "orchestrator": "langgraph",
        "cold_start": False,
        "clean_experiment": bool(clean_experiment),
    }

    graph = build_production_graph(ctx)
    try:
        final_state = graph.invoke(initial)
    except Exception as exc:
        ctx.experiment.close(
            state="error",
            summary={"error": str(exc), "run_id": run_id, "rounds_requested": rounds},
        )
        raise

    produced = list(final_state.get("produced") or [])
    saved = list(final_state.get("saved_factors") or [])
    screened_names = [p["name"] for p in produced if p.get("status") == "screened"]
    promo: dict[str, Any] = {
        "promoted": [],
        "held_screened": screened_names,
        "errors": [],
        "auto_promote": False,
    }
    if screened_names:
        from qfactor.factor.ops import LibraryOps

        prune = LibraryOps(ctx.cfg).prune_redundant_screened()
        promo["pruned_screened"] = prune.get("demoted") or []
    result = {
        "run_id": run_id,
        "experiment_id": manifest["experiment_id"],
        "experiment_manifest": str(ctx.experiment.manifest_path),
        "mode": "llm_first",
        "orchestrator": "langgraph",
        "theme": theme,
        "round_theme_last": final_state.get("round_theme"),
        "llm_ratio": llm_ratio,
        "llm_review_ratio": llm_review_ratio,
        "rounds": rounds,
        "produced": produced,
        "saved_total": saved,
        "production_promo": promo,
        "mechanism_hits": dict(final_state.get("mechanism_hits") or {}),
        "lessons_tail": list(final_state.get("lessons") or [])[-10:],
        "banned_skeletons": list(final_state.get("banned_skeletons") or [])[:20],
        "checkpoint": None if clean_experiment else str(ctx.checkpoint.path),
        "run_dir": str(run_dir),
        "status": (
            "candidate"
            if (promo.get("promoted") or any(p.get("status") == "candidate" for p in produced))
            else ("screened" if produced else "reject")
        ),
        "factor": produced[0]["name"] if produced else None,
        "clean_experiment": bool(clean_experiment),
    }
    experiment_summary = ctx.experiment.close(
        state="completed",
        summary={
            "rounds_requested": rounds,
            "rounds_completed": int(final_state.get("rounds_done") or 0),
            "saved_total": len(saved),
            "screened": len(screened_names),
            "trial_count": ctx.experiment.trial_count,
        },
    )
    result["experiment_state"] = experiment_summary["state"]
    result["trial_count"] = experiment_summary["trial_count"]
    (run_dir / "final.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return result
