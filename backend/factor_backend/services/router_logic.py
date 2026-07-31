from __future__ import annotations

from statistics import mean, median
from typing import Any


def decide_action(
    scores: list[float],
    veto: bool,
    *,
    round_idx: int,
    max_round: int,
    mean_min: float = 80.0,
    median_min: float = 75.0,
) -> dict[str, Any]:
    if len(scores) != 6:
        raise ValueError(f"expected 6 scores, got {len(scores)}")

    final_score = round(float(mean(scores)), 1)
    median_score = float(median(scores))
    save_ok = final_score >= mean_min and median_score >= median_min and not veto

    if save_ok:
        action = "SAVE"
    elif round_idx < max_round:
        action = "REVISE"
    else:
        action = "DROP"

    return {
        "final_score": final_score,
        "median_score": median_score,
        "veto": veto,
        "action": action,
    }


def merge_scorecards(
    previous: dict[str, dict[str, Any]],
    new_scores: dict[str, dict[str, Any]],
    changed_ids: list[str],
) -> dict[str, dict[str, Any]]:
    merged = {str(k): v for k, v in previous.items()}
    changed: set[str] = set()
    for x in changed_ids or []:
        if isinstance(x, str) and x:
            changed.add(x)
        elif isinstance(x, dict):
            fid = x.get("factor_id") or x.get("id")
            if fid is not None and not isinstance(fid, (dict, list)):
                changed.add(str(fid))
        elif x is not None:
            changed.add(str(x))
    for fid, card in new_scores.items():
        fid_s = str(fid)
        if fid_s in changed or fid_s not in merged:
            merged[fid_s] = card
    return merged
