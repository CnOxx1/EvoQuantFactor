from __future__ import annotations

from typing import Any

# Library lifecycle:
#   draft     — files exist / not yet research-pass (or eval reject)
#   screened  — research gate passed; not for downstream modules
#   candidate — production gate passed; usable by later modules
#   approved  — human confirmed
KEEP_STATUSES = ("screened", "candidate", "approved")
USABLE_STATUSES = ("candidate", "approved")


def _icir_annualized(thresholds: dict[str, Any]) -> bool:
    return bool(thresholds.get("icir_annualized", True))


def _icir_mode(thresholds: dict[str, Any]) -> str:
    mode = str(thresholds.get("icir_mode") or "").strip().lower()
    if mode:
        return mode
    return "window" if not _icir_annualized(thresholds) else "annualized"


def _icir_for_gate(metrics: dict[str, Any], thresholds: dict[str, Any]) -> float:
    """Production holdout uses Newey-West ICIR when icir_mode=newey_west."""
    mode = _icir_mode(thresholds)
    if mode == "newey_west":
        raw = metrics.get("icir_nw")
        if raw is None:
            raw = metrics.get("icir", 0.0)
        return abs(float(raw))
    if mode == "window" or not _icir_annualized(thresholds):
        return abs(float(metrics.get("icir", 0.0)))
    if "icir_ann" in metrics and metrics["icir_ann"] is not None:
        return abs(float(metrics["icir_ann"]))
    return abs(float(metrics.get("icir", 0.0)))


def _min_icir(thresholds: dict[str, Any]) -> float:
    mode = _icir_mode(thresholds)
    if mode in {"newey_west", "window"} or not _icir_annualized(thresholds):
        return float(thresholds.get("min_holdout_icir", thresholds.get("min_icir", 0.0)))
    return float(thresholds.get("min_icir", 0.0))


def _resid_icir_for_gate(metrics: dict[str, Any], thresholds: dict[str, Any]) -> float:
    mode = _icir_mode(thresholds)
    if mode == "newey_west":
        raw = metrics.get("resid_icir_nw")
        if raw is None:
            raw = metrics.get("resid_icir", 0.0)
        return abs(float(raw))
    if mode == "window" or not _icir_annualized(thresholds):
        return abs(float(metrics.get("resid_icir", 0.0)))
    return abs(float(metrics.get("resid_icir_ann", metrics.get("resid_icir", 0.0))))


def apply_gate(metrics: dict[str, Any], thresholds: dict[str, Any], mode: str = "research") -> dict[str, Any]:
    ic_mean = float(metrics.get("rank_ic_mean", 0.0))
    abs_ic = abs(ic_mean)
    checks = {
        "rank_ic_mean": ic_mean >= float(thresholds.get("min_rank_ic_mean", 0.0)),
        "abs_rank_ic": abs_ic >= float(thresholds.get("min_abs_rank_ic_mean", 0.0)),
        "icir": _icir_for_gate(metrics, thresholds) >= _min_icir(thresholds),
        "coverage": float(metrics.get("coverage", 0.0))
        >= float(thresholds.get("min_coverage", 0.0)),
        "max_corr": float(metrics.get("max_corr", 0.0))
        <= float(thresholds.get("max_corr_existing", 1.0)),
        "monotonic": float(metrics.get("monotonic_score", 0.0))
        >= float(thresholds.get("min_layered_monotonic_score", 0.0)),
        "turnover": float(metrics.get("daily_turnover", 0.0))
        <= float(thresholds.get("max_daily_turnover", 9.0)),
        "years": bool(metrics.get("years_consistent", False)),
    }
    if thresholds.get("require_no_lookahead", True):
        checks["no_lookahead"] = bool(metrics.get("no_lookahead", False))
    if thresholds.get("require_recent_ic_positive", False):
        min_recent = float(
            thresholds.get("min_recent_ic_mean", thresholds.get("min_abs_rank_ic_mean", 0.0))
        )
        checks["recent_ic"] = float(metrics.get("recent_rank_ic_mean", 0.0)) >= min_recent
    if thresholds.get("require_oos", False):
        min_oos = float(thresholds.get("min_oos_ic_mean", 0.01))
        min_folds = int(thresholds.get("min_oos_pos_folds", 1))
        min_fold_ic = float(
            metrics.get("oos_min_fold_ic", metrics.get("oos_ic_mean", 0.0))
        )
        oos_floor = (
            int(metrics.get("oos_pos_folds", 0)) >= min_folds
            and min_fold_ic >= min_oos
        )
        if str(thresholds.get("oos_mode") or "") == "freeze_sign":
            # Same-sign vs train, plus every holdout fold clearing min_oos_ic_mean.
            checks["oos"] = bool(metrics.get("freeze_sign_ok", False)) and oos_floor
        else:
            checks["oos"] = (
                float(metrics.get("oos_ic_mean", 0.0)) >= min_oos and oos_floor
            )
    if thresholds.get("require_train_ic", False):
        min_train = float(
            thresholds.get("min_train_ic_mean", thresholds.get("min_rank_ic_mean", 0.0))
        )
        checks["train_ic"] = float(metrics.get("train_rank_ic_mean", 0.0)) >= min_train
    if thresholds.get("require_cost_ls_positive", False):
        checks["cost_ls"] = float(metrics.get("cost_adjusted_ls", 0.0)) > 0
    if thresholds.get("require_residual_ic", False):
        checks["resid_ic"] = abs(float(metrics.get("resid_ic_mean", 0.0))) >= float(
            thresholds.get("min_resid_ic_mean", 0.01)
        ) and _resid_icir_for_gate(metrics, thresholds) >= float(
            thresholds.get("min_resid_icir", 0.0)
        )

    core = (
        checks["abs_rank_ic"]
        and checks["icir"]
        and checks["coverage"]
        and checks["max_corr"]
        and checks.get("no_lookahead", True)
    )
    passed = all(checks.values())

    if mode == "production":
        status = "candidate" if passed else "reject"
    else:
        # Soft pass may skip years/turnover, but never skip OOS or recent-IC
        # when those checks are on — otherwise inverted factors pollute screened.
        soft = core and checks["monotonic"]
        if "oos" in checks:
            soft = soft and checks["oos"]
        if "recent_ic" in checks:
            soft = soft and checks["recent_ic"]
        if "resid_ic" in checks:
            soft = soft and checks["resid_ic"]
        if passed or soft:
            status = "screened"
        else:
            status = "reject"
    return {"passed": passed, "status": status, "checks": checks, "mode": mode}


def route_library_status(
    gate_name: str,
    gate_status: str,
    current: str = "draft",
) -> str:
    """Map an eval gate result onto a durable library status."""
    if gate_status in KEEP_STATUSES:
        return gate_status
    # Only a production reject of a kept factor falls back to screened.
    if gate_name == "production" and current in KEEP_STATUSES:
        return "screened"
    return "draft"
