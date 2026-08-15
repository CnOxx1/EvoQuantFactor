from __future__ import annotations

from typing import Any


def classify_research_cohort(item: dict[str, Any]) -> dict[str, Any]:
    """Classify parent eligibility without rewriting historical factor files."""
    source = str(item.get("source") or "")
    status = str(item.get("status") or "")
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    declared = str(params.get("research_cohort") or "")

    if source == "seed":
        return {
            "cohort": "fixed_seed",
            "parent_eligible": True,
            "candidate_eligible": False,
            "reason": "fixed_dsl_seed",
        }
    if declared in {"clean_discovery", "current_discovery"}:
        return {
            "cohort": declared,
            "parent_eligible": status in {"screened", "candidate", "approved"},
            "candidate_eligible": status in {"candidate", "approved"},
            "reason": "clean_experiment",
        }
    if status in {"candidate", "approved"}:
        return {
            "cohort": "verified_candidate",
            "parent_eligible": True,
            "candidate_eligible": True,
            "reason": "passed_candidate_contract",
        }

    universe = str(summary.get("universe_mode") or "").lower()
    circ_mv = str(summary.get("circ_mv_source") or "").lower()
    if universe in {"snapshot", "freeze_start"} or circ_mv == "estimated":
        return {
            "cohort": "legacy_snapshot_research",
            "parent_eligible": False,
            "candidate_eligible": False,
            "reason": "snapshot_or_estimated_size",
        }
    return {
        "cohort": "unverified_research",
        "parent_eligible": False,
        "candidate_eligible": False,
        "reason": "missing_clean_experiment_provenance",
    }
