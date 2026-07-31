from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    # identity
    job_id: str
    report_id: str
    report: str
    report_title: str
    meta: dict[str, Any]

    # loop control
    round: int
    max_round: int
    mean_min: float
    median_min: float
    llm_mock: bool
    force_end: bool
    route: str  # revise | persist

    # working set
    revise_packet: dict[str, Any] | None
    extract: dict[str, Any]
    factors: list[dict[str, Any]]
    changed_ids: list[str]
    frozen: dict[str, dict[str, Any]]
    prev_scorecards: dict[str, dict[str, Any]]
    new_scorecards: dict[str, dict[str, Any]]
    scorecards: dict[str, dict[str, Any]]
    dropped: list[dict[str, Any]]
    gate_rows: list[dict[str, Any]]
    revise_items: list[dict[str, Any]]

    # output
    saved_factors: list[dict[str, Any]]
    result: dict[str, Any]
    errors: list[str]
    engine: str
    attempt: int
