from __future__ import annotations

from typing import Any


LEVELS = ("official", "verified", "derived", "estimated", "missing")


def evidence_quality(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Classify provenance without converting free/derived data into vendor proof."""
    limitations = " ".join(str(x) for x in (meta.get("limitations") or [])).lower()
    universe_mode = str(meta.get("universe_mode") or "").lower()
    if not universe_mode and "snapshot" in limitations:
        universe_mode = "snapshot"
    circ_mv_source = str(meta.get("circ_mv_source") or "").lower()
    if not circ_mv_source and "circ_mv estimated" in limitations:
        circ_mv_source = "estimated"

    universe_level = (
        "verified"
        if universe_mode == "pit"
        else ("official" if universe_mode == "snapshot" else "missing")
    )
    circ_level = (
        "verified"
        if circ_mv_source.endswith("_daily_basic") and circ_mv_source != "estimated"
        else ("estimated" if circ_mv_source == "estimated" else "missing")
    )

    def covered(key: str, *, derived: bool = False) -> str:
        if float(meta.get(key) or 0.0) <= 0.0:
            return "missing"
        return "derived" if derived else "verified"

    return {
        "universe": {
            "level": universe_level,
            "source": (meta.get("members_provider") or {}).get("provider"),
            "mode": universe_mode or "unknown",
            "candidate_eligible": universe_mode == "pit",
        },
        "circ_mv": {
            "level": circ_level,
            "source": circ_mv_source or "none",
            "candidate_eligible": circ_level == "verified",
        },
        "security_status": {
            "level": covered("security_status_coverage"),
            "source": meta.get("security_status_provider"),
        },
        "adv_20d": {
            "level": covered("adv_20d_coverage", derived=True),
            "source": "rolling_completed_amount" if meta.get("adv_20d_coverage") else None,
        },
        "corporate_actions": {
            "level": covered("corporate_action_coverage"),
            "source": meta.get("corporate_actions_provider"),
        },
        "industry": {
            "level": covered("industry_pit_coverage"),
            "source": meta.get("industry_provider"),
            "candidate_eligible": float(meta.get("industry_pit_coverage") or 0.0) > 0.0,
        },
        "risk_exposures": {
            "level": covered("risk_exposures_coverage"),
            "source": meta.get("risk_exposures_provider"),
        },
    }
