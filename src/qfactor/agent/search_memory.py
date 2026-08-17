from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qfactor.agent.diversity import expression_fingerprint
from qfactor.settings import ProjectConfig, get_project_config

SCHEMA_VERSION = 1
MAX_HASHES = 4000
MAX_SKELETONS = 500
MAX_TRACES = 40
MAX_THEMES = 12

# Same-panel exact formulas that already failed a full eval. Re-running them
# on the same data_version is wasted work, not exploration.
HASH_REASONS = frozenset(
    {
        "weak_ic",
        "high_corr",
        "gate_reject",
        "banned_skeleton",
        "duplicate_expr",
        "review_reject",
    }
)
# Window variants of a high-corr skeleton stay collinear with the same parents.
# Cheap/weak IC is NOT skeleton-wide: another window can still clear the bar.
SKELETON_REASONS = frozenset({"high_corr"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def failed_checks_from_report(report: dict[str, Any] | None) -> list[str]:
    """Names of research-gate checks that were False. Empty if the gate did not run."""
    if not isinstance(report, dict):
        return []
    gate = report.get("gate") if isinstance(report.get("gate"), dict) else {}
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    return sorted(str(k) for k, v in checks.items() if v is False)


def compact_reject_trace(lesson: dict[str, Any]) -> str | None:
    """One ASI line: why this skeleton failed, never holdout resid/cost numbers."""
    if not isinstance(lesson, dict):
        return None
    detail = lesson.get("detail") if isinstance(lesson.get("detail"), dict) else {}
    reason = str(lesson.get("reason") or detail.get("reason") or "").strip()
    sk = str(detail.get("skeleton") or "").strip()
    if not sk:
        expr = lesson.get("expression")
        if expr:
            try:
                sk = str(expression_fingerprint(str(expr))["skeleton"])
            except Exception:
                sk = ""
    failed = detail.get("failed_checks") or []
    if isinstance(failed, dict):
        failed = [k for k, v in failed.items() if v is False]
    if not isinstance(failed, list):
        failed = []
    failed = [str(x) for x in failed if str(x)]
    bits: list[str] = []
    if reason:
        bits.append(reason)
    if sk:
        bits.append(f"skeleton {sk}")
    if failed:
        bits.append("failed_checks=" + ",".join(failed[:6]))
    ic = _safe_float(detail.get("rank_ic_mean"))
    if ic is not None and reason in {"weak_ic", "gate_reject"}:
        bits.append(f"|IC|={abs(ic):.4f}")
    corr = _safe_float(detail.get("max_corr"))
    if corr is not None and reason == "high_corr":
        bits.append(f"corr={corr:.2f}")
    if not bits:
        return None
    return " ".join(bits) + "; change operator/field/subtree, never window-only"


def _parent_tier(row: dict[str, Any]) -> int:
    st = str(row.get("status") or "draft")
    return {"approved": 0, "candidate": 1, "screened": 2, "draft": 3}.get(st, 4)


def parent_objectives(row: dict[str, Any]) -> tuple[float, float, float, float]:
    """Orthogonal axes for a Pareto parent archive. Higher is better on every axis.

    Residual IC is diagnosis, not a gate. Uniqueness is 1-max_corr so clones
    of an existing keep book lose to an equal-IC orthogonal parent.
    """
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    ic = abs(float(summary.get("rank_ic_mean") or 0.0))
    resid = abs(float(summary.get("resid_ic_mean") or 0.0))
    corr = abs(float(summary.get("max_corr") or 0.0))
    uniqueness = 1.0 - min(1.0, corr)
    years = summary.get("years")
    if isinstance(years, dict):
        year_score = float(years.get("dominant_years") or 0.0)
    else:
        year_score = 1.0 if summary.get("years_consistent") else 0.0
    return (ic, resid, uniqueness, year_score)


def _dominates(
    left: tuple[float, ...], right: tuple[float, ...]
) -> bool:
    return all(a >= b for a, b in zip(left, right)) and any(a > b for a, b in zip(left, right))


def _skeleton_of(row: dict[str, Any]) -> str:
    expr = str(row.get("expression") or "")
    if not expr:
        return ""
    try:
        return str(expression_fingerprint(expr)["skeleton"])
    except Exception:
        return expr


def collapse_same_skeleton(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per skeleton: better lifecycle tier, then residual, then IC."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in pool:
        sk = _skeleton_of(row)
        key = sk or f"__anon_{id(row)}"
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        rank = (
            _parent_tier(row),
            -abs(float(summary.get("resid_ic_mean") or 0.0)),
            -abs(float(summary.get("rank_ic_mean") or 0.0)),
        )
        prev = best.get(key)
        if prev is None:
            best[key] = row
            order.append(key)
            continue
        prev_s = prev.get("summary") if isinstance(prev.get("summary"), dict) else {}
        prev_rank = (
            _parent_tier(prev),
            -abs(float(prev_s.get("resid_ic_mean") or 0.0)),
            -abs(float(prev_s.get("rank_ic_mean") or 0.0)),
        )
        if rank < prev_rank:
            best[key] = row
    return [best[k] for k in order]


def pareto_select_parents(
    pool: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Keep non-dominated parents, then fill by rank. Not a family quota.

    An amplitude row with the best residual IC stays even if another amplitude
    row has a higher raw IC. Window-shopped clones are collapsed first.
    """
    if limit <= 0 or not pool:
        return []
    collapsed = collapse_same_skeleton(pool)
    objs = [parent_objectives(row) for row in collapsed]
    front_idx = [
        i
        for i, obj in enumerate(objs)
        if not any(
            j != i and _dominates(objs[j], obj) for j in range(len(objs))
        )
    ]
    rest_idx = [i for i in range(len(collapsed)) if i not in set(front_idx)]

    def _rank_idx(i: int) -> tuple[int, float, float]:
        row = collapsed[i]
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        return (
            _parent_tier(row),
            -abs(float(summary.get("resid_ic_mean") or 0.0)),
            -abs(float(summary.get("rank_ic_mean") or 0.0)),
        )

    picked: list[int] = []
    seen_mech: set[str] = set()
    front_idx.sort(key=_rank_idx)
    # Round-robin mechanisms on the front so one family cannot occupy every slot.
    remaining_front = list(front_idx)
    while remaining_front and len(picked) < limit:
        progressed = False
        for i in list(remaining_front):
            mid = str(collapsed[i].get("mechanism") or collapsed[i].get("category") or "")
            if mid and mid in seen_mech:
                continue
            picked.append(i)
            remaining_front.remove(i)
            if mid:
                seen_mech.add(mid)
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            picked.extend(remaining_front[: max(0, limit - len(picked))])
            remaining_front = remaining_front[max(0, limit - len(picked)) :]
            break
    if len(picked) < limit:
        rest_idx.sort(key=_rank_idx)
        for i in rest_idx:
            if i in picked:
                continue
            picked.append(i)
            if len(picked) >= limit:
                break
    return [collapsed[i] for i in picked[:limit]]


class SearchMemory:
    """Panel-scoped search archive that survives clean_experiment cycles.

    This is not a GEPA dependency. It stores failed exact formulas, high-corr
    skeletons, recent themes, and structured reject traces for mutate ASI.
    Keep-inventory counts are intentionally absent: they must not ban a family.
    """

    def __init__(
        self,
        *,
        data_version: str,
        path: Path,
        payload: dict[str, Any] | None = None,
    ):
        self.data_version = str(data_version or "")
        self.path = path
        raw = payload if isinstance(payload, dict) else {}
        stored_dv = str(raw.get("data_version") or "")
        if stored_dv != self.data_version:
            raw = {}
        self._hashes: list[str] = [
            str(x) for x in (raw.get("failed_hashes") or []) if str(x)
        ]
        self._hash_set = set(self._hashes)
        skel_raw = raw.get("failed_skeletons") or {}
        self._skeletons: dict[str, dict[str, Any]] = {}
        if isinstance(skel_raw, dict):
            for key, meta in skel_raw.items():
                sk = str(key)
                if not sk:
                    continue
                self._skeletons[sk] = dict(meta) if isinstance(meta, dict) else {"reason": "high_corr", "n": 1}
        self._traces: list[dict[str, Any]] = [
            dict(t) for t in (raw.get("traces") or []) if isinstance(t, dict)
        ]
        self._recent_themes: list[str] = [
            str(x) for x in (raw.get("recent_themes") or []) if str(x)
        ]

    @classmethod
    def path_for(cls, cfg: ProjectConfig | None = None) -> Path:
        cfg = cfg or get_project_config()
        return cfg.path("runs") / "search_memory.json"

    @classmethod
    def load(
        cls,
        cfg: ProjectConfig | None = None,
        *,
        data_version: str,
    ) -> "SearchMemory":
        cfg = cfg or get_project_config()
        path = cls.path_for(cfg)
        payload: dict[str, Any] | None = None
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except (OSError, json.JSONDecodeError):
                payload = None
        return cls(data_version=data_version, path=path, payload=payload)

    def failed_hashes(self) -> list[str]:
        return list(self._hashes[-MAX_HASHES:])

    def failed_skeletons(self) -> list[str]:
        return list(self._skeletons.keys())[-MAX_SKELETONS:]

    def recent_themes(self) -> list[str]:
        return list(self._recent_themes[-MAX_THEMES:])

    def traces(self) -> list[dict[str, Any]]:
        return list(self._traces[-MAX_TRACES:])

    def as_lessons(self) -> list[dict[str, Any]]:
        """Reload traces as ASI lessons. skip_prior so they do not boost field priors."""
        out: list[dict[str, Any]] = []
        for trace in self.traces():
            detail = {
                "skeleton": trace.get("skeleton"),
                "failed_checks": list(trace.get("failed_checks") or []),
                "rank_ic_mean": trace.get("rank_ic_mean"),
                "max_corr": trace.get("max_corr"),
                "stage": trace.get("stage"),
                "skip_prior": True,
            }
            if trace.get("cheap"):
                detail["cheap"] = True
            out.append(
                {
                    "mechanism": trace.get("mechanism"),
                    "reason": trace.get("reason"),
                    "expression": trace.get("expression"),
                    "detail": detail,
                }
            )
        return out

    def set_recent_themes(self, themes: list[str] | None) -> None:
        self._recent_themes = [str(x) for x in (themes or []) if str(x)][-MAX_THEMES:]

    def record_reject(
        self,
        *,
        expression: str | None,
        mechanism: str | None,
        reason: str,
        stage: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        info = dict(detail or {})
        expr = str(expression or "")
        expr_hash = str(info.get("expr_hash") or "")
        sk = str(info.get("skeleton") or "")
        if expr and (not expr_hash or not sk):
            try:
                fp = expression_fingerprint(expr)
                expr_hash = expr_hash or str(fp.get("expr_hash") or "")
                sk = sk or str(fp.get("skeleton") or "")
            except Exception:
                pass
        if reason in HASH_REASONS and expr_hash and expr_hash not in self._hash_set:
            self._hashes.append(expr_hash)
            self._hash_set.add(expr_hash)
            if len(self._hashes) > MAX_HASHES:
                dropped = self._hashes[:-MAX_HASHES]
                self._hashes = self._hashes[-MAX_HASHES:]
                self._hash_set.difference_update(dropped)
        if reason in SKELETON_REASONS and sk:
            meta = self._skeletons.get(sk) or {"reason": reason, "n": 0}
            meta["reason"] = reason
            meta["n"] = int(meta.get("n") or 0) + 1
            failed = info.get("failed_checks") or []
            if isinstance(failed, list) and failed:
                meta["failed_checks"] = [str(x) for x in failed[:8]]
            self._skeletons[sk] = meta
            if len(self._skeletons) > MAX_SKELETONS:
                extra = list(self._skeletons.keys())[:-MAX_SKELETONS]
                for key in extra:
                    self._skeletons.pop(key, None)
        failed = info.get("failed_checks") or []
        if isinstance(failed, dict):
            failed = [k for k, v in failed.items() if v is False]
        if not isinstance(failed, list):
            failed = []
        trace = {
            "mechanism": str(mechanism or "unknown"),
            "reason": str(reason),
            "stage": str(stage),
            "skeleton": sk or None,
            "expression": expr or None,
            "failed_checks": [str(x) for x in failed if str(x)][:8],
            "rank_ic_mean": _safe_float(info.get("rank_ic_mean")),
            "max_corr": _safe_float(info.get("max_corr")),
        }
        if info.get("cheap"):
            trace["cheap"] = True
        self._traces.append(trace)
        self._traces = self._traces[-MAX_TRACES:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "data_version": self.data_version,
            "updated_at": _utc_now(),
            "recent_themes": self.recent_themes(),
            "failed_hashes": self.failed_hashes(),
            "failed_skeletons": {
                sk: dict(meta) for sk, meta in list(self._skeletons.items())[-MAX_SKELETONS:]
            },
            "traces": self.traces(),
            "notes": [
                "Panel-scoped search archive. Reset when data_version changes.",
                "Does not store keep-inventory counts and must not ban a mechanism family.",
                "failed_hashes skip exact re-eval; failed_skeletons are high-corr only.",
            ],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def seed_clean_checkpoint(memory: SearchMemory) -> dict[str, Any]:
    """Checkpoint slice for a new clean experiment that still remembers search."""
    failed_sk = memory.failed_skeletons()
    return {
        "iteration": 0,
        "tested_hashes": [],
        "saved_factors": [],
        "mechanism_hits": {},
        "history": [],
        "lessons": memory.as_lessons(),
        "banned_skeletons": failed_sk,
        "banned_hashes": memory.failed_hashes(),
        "high_corr_skeletons": failed_sk,
        "recent_themes": memory.recent_themes(),
        "last_catalog_expand_round": None,
    }
