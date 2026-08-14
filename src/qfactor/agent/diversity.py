from __future__ import annotations

import random
import time
from collections import Counter
from typing import Any

from qfactor.eval.gate import KEEP_STATUSES, USABLE_STATUSES
from qfactor.dsl.parser import expr_hash, parse_expression, skeleton
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config


def unique_factor_name(mechanism: str, prefix: str | None = None) -> str:
    """Always unique: {mech}_{prefix}_{ts}_{rand} — never bare names like reversal_5d."""
    mech = "".join(c if c.isalnum() or c == "_" else "_" for c in (mechanism or "factor"))
    mech = mech.strip("_").lower() or "factor"
    tag = "".join(c if c.isalnum() or c == "_" else "_" for c in (prefix or "x"))
    tag = tag.strip("_").lower()[:24] or "x"
    ts = time.strftime("%H%M%S")
    return f"{mech}_{tag}_{ts}_{random.randint(1000, 9999)}"


def expression_fingerprint(expr_text: str) -> dict[str, str]:
    expr = parse_expression(expr_text)
    return {
        "expr_hash": expr_hash(expr),
        "skeleton": skeleton(expr),
        "canonical": expr.to_str(),
    }


def library_diversity_index(cfg: ProjectConfig | None = None) -> dict[str, Any]:
    """
    Collect expr hashes / skeletons / FSA bans from factor_lib.

    - Exact expr hashes: all factors (avoid regenerating identical formulas)
    - FSA skeleton frequency: only candidate/approved (reject pile must not freeze search)
    - High-corr rejects still contribute their skeleton to an explicit ban list
    """
    cfg = cfg or get_project_config()
    prod = (cfg.project.get("production") or {}).get("diversity") or {}
    fsa_ratio = float(prod.get("fsa_ratio", 0.15))
    max_corr_ban = float(prod.get("max_corr_ban", 0.95))

    reg = FactorRegistry(cfg)
    hashes: set[str] = set()
    survivor_skels: list[str] = []
    high_corr_skels: set[str] = set()
    expressions: list[str] = []
    for item in reg.list_factors():
        name = item.get("name")
        if not name:
            continue
        status = item.get("status") or "draft"
        try:
            spec = reg.load_spec(str(name))
        except Exception:
            continue
        expr = spec.expression or (spec.params or {}).get("expression")
        if not expr:
            continue
        try:
            fp = expression_fingerprint(str(expr))
        except Exception:
            continue
        hashes.add(fp["expr_hash"])
        expressions.append(fp["canonical"])
        summary = item.get("summary") or {}
        if status in {"candidate", "approved"}:
            survivor_skels.append(fp["skeleton"])
        if float(summary.get("max_corr") or 0) >= max_corr_ban and (
            summary.get("status") == "reject" or status == "draft"
        ):
            high_corr_skels.add(fp["skeleton"])
            hashes.add(fp["expr_hash"])

    counts = Counter(survivor_skels)
    n = max(len(survivor_skels), 1)
    fsa_banned = {s for s, c in counts.items() if (c / n) >= fsa_ratio and c >= 2}
    banned_skels = sorted(fsa_banned | high_corr_skels)
    return {
        "expr_hashes": sorted(hashes),
        "skeletons": survivor_skels,
        "skeleton_counts": dict(counts),
        "banned_skeletons": banned_skels,
        "expressions": expressions,
        "fsa_ratio": fsa_ratio,
    }


def skeleton_keep_counts(cfg: ProjectConfig | None = None) -> dict[str, int]:
    """How many screened/candidate/approved factors share each skeleton."""
    cfg = cfg or get_project_config()
    reg = FactorRegistry(cfg)
    counts: Counter[str] = Counter()
    for item in reg.list_factors():
        if item.get("status") not in KEEP_STATUSES:
            continue
        try:
            spec = reg.load_spec(str(item["name"]))
            expr = spec.expression or (spec.params or {}).get("expression")
            if not expr:
                continue
            counts[expression_fingerprint(str(expr))["skeleton"]] += 1
        except Exception:
            continue
    return dict(counts)


def saturated_skeletons(
    cfg: ProjectConfig | None = None, max_per: int = 2
) -> set[str]:
    """Skeletons that already have enough kept factors — stop window shopping."""
    return {sk for sk, n in skeleton_keep_counts(cfg).items() if n >= max_per}


def active_skeleton_bans(
    cfg: ProjectConfig | None = None,
    extra: list[str] | None = None,
    max_per: int | None = None,
    cold_start: bool = False,
) -> set[str]:
    """
    Live skeleton bans: high-corr / FSA from the library, plus saturated
    kept-skeletons. Does not replay a checkpoint graveyard of one-off accepts.

    Cold start: only exact hashes matter — FSA and per-skeleton caps stay off
    until there are enough parents to evolve.
    """
    if cold_start:
        return set()
    cfg = cfg or get_project_config()
    prod = (cfg.project.get("production") or {}).get("diversity") or {}
    if max_per is None:
        max_per = int(prod.get("max_per_skeleton", 2))
    index = library_diversity_index(cfg)
    return set(index.get("banned_skeletons") or []) | saturated_skeletons(cfg, max_per) | set(
        extra or []
    )


def merge_bans(
    index: dict[str, Any],
    extra_hashes: list[str] | None = None,
    extra_skeletons: list[str] | None = None,
) -> dict[str, set[str]]:
    return {
        "hashes": set(index.get("expr_hashes") or []) | set(extra_hashes or []),
        "skeletons": set(index.get("banned_skeletons") or []) | set(extra_skeletons or []),
    }


def is_banned_expression(
    expr_text: str,
    bans: dict[str, set[str]],
    parent_skeleton: str | None = None,
) -> tuple[bool, str]:
    try:
        fp = expression_fingerprint(expr_text)
    except Exception as e:
        return True, f"parse_error:{e}"
    if fp["expr_hash"] in bans.get("hashes", set()):
        return True, "duplicate_expr"
    if fp["skeleton"] in bans.get("skeletons", set()):
        return True, "banned_skeleton"
    if parent_skeleton and fp["skeleton"] == parent_skeleton:
        return True, "same_parent_skeleton"
    return False, ""


def record_lesson(
    lessons: list[dict[str, Any]],
    *,
    mechanism: str,
    reason: str,
    expression: str | None = None,
    detail: dict[str, Any] | None = None,
    keep: int = 200,
) -> list[dict[str, Any]]:
    row = {
        "mechanism": mechanism,
        "reason": reason,
        "expression": expression,
        "detail": detail or {},
    }
    out = list(lessons) + [row]
    return out[-keep:]


def weak_mechanisms(lessons: list[dict[str, Any]], recent: int = 30) -> dict[str, int]:
    """Count recent failure reasons by mechanism (for Decide demotion)."""
    counts: dict[str, int] = {}
    for lesson in lessons[-recent:]:
        if lesson.get("reason") in {"weak_ic", "high_corr", "gate_reject", "banned_skeleton"}:
            m = str(lesson.get("mechanism") or "unknown")
            counts[m] = counts.get(m, 0) + 1
    return counts


def _mechanism_coverage(
    existing: list[dict[str, Any]] | None, statuses: tuple[str, ...]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    allowed = set(statuses)
    for item in existing or []:
        if str(item.get("status") or "") not in allowed:
            continue
        mid = str(item.get("mechanism") or item.get("category") or "").strip()
        if mid:
            counts[mid] = counts.get(mid, 0) + 1
    return counts


def keep_mechanism_coverage(existing: list[dict[str, Any]] | None) -> dict[str, int]:
    """Count screened+candidate+approved by mechanism (KEEP inventory, not generation hits)."""
    return _mechanism_coverage(existing, KEEP_STATUSES)


def usable_mechanism_coverage(existing: list[dict[str, Any]] | None) -> dict[str, int]:
    """Count candidate/approved factors by mechanism so mining fills missing families."""
    return _mechanism_coverage(existing, USABLE_STATUSES)


def blocked_mechanisms(usable_coverage: dict[str, int] | None) -> set[str]:
    """Families that already have at least one production factor."""
    return {str(k) for k, n in (usable_coverage or {}).items() if int(n) >= 1}


def eligible_mechanisms(
    mechanisms: list[dict[str, Any]],
    usable_coverage: dict[str, int] | None,
) -> list[dict[str, Any]]:
    """Mechanisms with zero candidate/approved factors; fall back to all if none remain."""
    blocked = blocked_mechanisms(usable_coverage)
    kept = [m for m in mechanisms if m.get("id") not in blocked]
    return kept if kept else list(mechanisms)


def pick_theme_with_lessons(
    mechanisms: list[dict[str, Any]],
    coverage: dict[str, int],
    lessons: list[dict[str, Any]],
    forced: str | None = None,
    soft_switch_after: int = 3,
    recent_themes: list[str] | None = None,
    hard_rotate: bool = True,
    usable_coverage: dict[str, int] | None = None,
) -> str | None:
    """
    Prefer under-covered mechanisms; demote repeated failures.
    hard_rotate: avoid repeating recent themes when alternatives exist.
    usable_coverage: families with production factors are excluded while alternatives exist.
    """
    pool = eligible_mechanisms(mechanisms, usable_coverage)
    ids = [m["id"] for m in pool]
    recent = list(recent_themes or [])
    if forced and forced in ids:
        fails = weak_mechanisms(lessons).get(forced, 0)
        if fails < soft_switch_after:
            return forced
    weak = weak_mechanisms(lessons)
    scored: list[tuple[int, str]] = []
    for m in pool:
        mid = m["id"]
        score = coverage.get(mid, 0) + 2 * weak.get(mid, 0)
        if hard_rotate and mid in recent[-3:]:
            score += 5  # push recently used themes down
        scored.append((score, mid))
    scored.sort()
    return scored[0][1] if scored else None
