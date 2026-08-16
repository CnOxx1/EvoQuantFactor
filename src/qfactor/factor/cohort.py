from __future__ import annotations

from typing import Any


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("summary") if isinstance(item.get("summary"), dict) else {}


def _params(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("params") if isinstance(item.get("params"), dict) else {}


def is_legacy_snapshot_evidence(item: dict[str, Any]) -> bool:
    summary = _summary(item)
    universe = str(summary.get("universe_mode") or "").lower()
    circ_mv = str(summary.get("circ_mv_source") or "").lower()
    return universe in {"snapshot", "freeze_start"} or circ_mv == "estimated"


def same_data_version(item: dict[str, Any], current_data_version: str | None) -> bool:
    """Seeds may lack a panel version; other parents must match the live panel."""
    if str(item.get("source") or "") == "seed":
        return True
    current = str(current_data_version or "").strip()
    if not current:
        return True
    dv = str(_summary(item).get("data_version") or "").strip()
    return bool(dv) and dv == current


def classify_research_cohort(item: dict[str, Any]) -> dict[str, Any]:
    """Classify parent eligibility without rewriting historical factor files."""
    source = str(item.get("source") or "")
    status = str(item.get("status") or "")
    declared = str(_params(item).get("research_cohort") or "")

    if source == "seed":
        return {
            "cohort": "fixed_seed",
            "parent_eligible": True,
            "candidate_eligible": False,
            "reason": "fixed_dsl_seed",
        }
    # Declared clean_discovery cannot override snapshot / estimated-size evidence.
    if is_legacy_snapshot_evidence(item):
        return {
            "cohort": "legacy_snapshot_research",
            "parent_eligible": False,
            "candidate_eligible": False,
            "reason": "snapshot_or_estimated_size",
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
    return {
        "cohort": "unverified_research",
        "parent_eligible": False,
        "candidate_eligible": False,
        "reason": "missing_clean_experiment_provenance",
    }


def apply_parent_eligibility(
    item: dict[str, Any],
    current_data_version: str | None = None,
) -> dict[str, Any]:
    """Cohort label plus live-panel parent eligibility."""
    out = classify_research_cohort(item)
    if out.get("parent_eligible") and not same_data_version(item, current_data_version):
        return {
            **out,
            "parent_eligible": False,
            "reason": "data_version_mismatch",
        }
    return out
