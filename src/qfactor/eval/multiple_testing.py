from __future__ import annotations

from statistics import NormalDist
from typing import Any


def familywise_ic_audit(
    metrics: dict[str, Any],
    *,
    n_trials: int,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Conservative one-sided Bonferroni audit for a searched factor.

    This is not a claim of full PBO/CSCV coverage. It is a fail-closed first
    release gate based on the exact number of generated trials in the experiment
    ledger. PBO/CSCV can be added when raw cross-validation scores are retained.
    """
    tests = max(1, int(n_trials))
    alpha = float(alpha)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")
    p_raw = float(metrics.get("p_value_nw_one_sided") or 1.0)
    mean = float(metrics.get("rank_ic_mean") or 0.0)
    se = float(metrics.get("se_nw") or 0.0)
    alpha_per_test = alpha / tests
    z_required = float(NormalDist().inv_cdf(1.0 - alpha_per_test))
    required_ic = z_required * se
    adjusted_p = min(1.0, p_raw * tests)
    passed = bool(mean > 0.0 and adjusted_p <= alpha and mean >= required_ic)
    return {
        "contract_version": "familywise_ic_audit_v1",
        "method": "bonferroni_one_sided_normal_nw",
        "state": "familywise_passed" if passed else "familywise_failed",
        "n_trials": tests,
        "alpha": alpha,
        "alpha_per_test": alpha_per_test,
        "p_value_nw_one_sided": p_raw,
        "adjusted_p_value": adjusted_p,
        "rank_ic_mean": mean,
        "se_nw": se,
        "required_rank_ic_mean": required_ic,
        "passed": passed,
        "limitations": [
            "This gate controls family-wise false positives using recorded trials.",
            "PBO/CSCV requires retained fold-level candidate score matrices and remains a future enhancement.",
        ],
    }


def research_selection_bias_preview(
    metrics: dict[str, Any],
    *,
    n_trials: int,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Expose trial-count risk without turning the research gate into production.

    Screened factors are hypotheses and may remain useful parents even when this
    conservative preview fails.  Sealed acceptance remains the binding
    family-wise gate.
    """
    audit = familywise_ic_audit(metrics, n_trials=n_trials, alpha=alpha)
    return {
        **audit,
        "contract_version": "research_selection_bias_preview_v1",
        "informational_only": True,
        "research_status_unchanged": True,
    }
