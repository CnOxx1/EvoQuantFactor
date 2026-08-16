from types import SimpleNamespace

import pandas as pd

from qfactor.agent.graph import _eligible_research_library, _node_persist
from qfactor.eval.service import EvalService
from qfactor.factor.cohort import (
    apply_parent_eligibility,
    classify_research_cohort,
    evidence_from_latest_report,
)


def test_snapshot_screened_is_legacy_and_not_parent():
    out = classify_research_cohort(
        {
            "status": "screened",
            "source": "compose",
            "summary": {
                "universe_mode": "snapshot",
                "circ_mv_source": "estimated",
            },
        }
    )
    assert out["cohort"] == "legacy_snapshot_research"
    assert out["parent_eligible"] is False
    assert out["candidate_eligible"] is False


def test_declared_clean_snapshot_is_still_legacy():
    out = classify_research_cohort(
        {
            "status": "screened",
            "source": "llm",
            "params": {"research_cohort": "clean_discovery"},
            "summary": {
                "universe_mode": "snapshot",
                "circ_mv_source": "estimated",
            },
        }
    )
    assert out["cohort"] == "legacy_snapshot_research"
    assert out["parent_eligible"] is False


def test_latest_report_fills_missing_catalog_data_version():
    summary = {
        "universe_mode": "pit",
        "circ_mv_source": "archive_daily_basic",
    }
    filled = evidence_from_latest_report(
        summary,
        {"metrics": {"data_version": "20260816T073655Z", "n_peers": 0}},
    )
    assert filled["data_version"] == "20260816T073655Z"
    item = {
        "status": "screened",
        "source": "llm",
        "params": {"research_cohort": "clean_discovery"},
        "summary": filled,
    }
    assert apply_parent_eligibility(item, "20260816T073655Z")["parent_eligible"] is True
    assert apply_parent_eligibility(item, "other_panel")["parent_eligible"] is False


def test_data_version_mismatch_is_not_a_parent():
    item = {
        "status": "screened",
        "source": "llm",
        "params": {"research_cohort": "clean_discovery"},
        "summary": {
            "universe_mode": "pit",
            "circ_mv_source": "archive_daily_basic",
            "data_version": "old_panel",
        },
    }
    assert classify_research_cohort(item)["parent_eligible"] is True
    out = apply_parent_eligibility(item, "new_panel")
    assert out["parent_eligible"] is False
    assert out["reason"] == "data_version_mismatch"
    assert apply_parent_eligibility(item, "old_panel")["parent_eligible"] is True


def test_clean_experiment_uses_seeds_and_current_experiment_only():
    pit = {
        "universe_mode": "pit",
        "circ_mv_source": "archive_daily_basic",
        "data_version": "live",
    }
    rows = [
        {"name": "seed", "source": "seed", "status": "draft"},
        {
            "name": "legacy",
            "source": "compose",
            "status": "screened",
            "summary": {"universe_mode": "snapshot", "circ_mv_source": "estimated"},
        },
        {
            "name": "current",
            "source": "llm",
            "status": "screened",
            "params": {"experiment_id": "exp_current", "research_cohort": "clean_discovery"},
            "summary": pit,
        },
        {
            "name": "other_clean",
            "source": "llm",
            "status": "screened",
            "params": {"experiment_id": "exp_other", "research_cohort": "clean_discovery"},
            "summary": pit,
        },
    ]
    ctx = SimpleNamespace(
        registry=SimpleNamespace(existing_summaries=lambda: rows)
    )
    kept = _eligible_research_library(
        ctx,
        {"clean_experiment": True, "experiment_id": "exp_current"},
    )
    assert {row["name"] for row in kept} == {"seed", "current"}


def test_clean_experiment_does_not_write_shared_checkpoint():
    saved = []
    ctx = SimpleNamespace(
        checkpoint=SimpleNamespace(save=lambda payload: saved.append(payload)),
        generator=SimpleNamespace(llm_cfg={}),
    )
    persist = _node_persist(ctx)
    out = persist(
        {
            "clean_experiment": True,
            "history": [],
            "round_stats": {},
            "rounds_done": 1,
            "tested_hashes": [],
            "saved_factors": [],
            "mechanism_hits": {},
            "lessons": [],
            "banned_skeletons": [],
            "banned_hashes": [],
            "high_corr_skeletons": [],
            "recent_themes": [],
        }
    )
    assert saved == []
    assert out["round_stats"]["catalog_expand"]["reason"] == "clean_experiment"


def test_clean_evaluation_excludes_legacy_correlation_peers():
    panel = pd.DataFrame({"a": [1.0, 2.0]}, index=["20240102", "20240103"])

    class _Registry:
        def existing_summaries(self):
            return [
                {"name": "legacy", "source": "compose", "params": {}},
                {
                    "name": "current",
                    "source": "llm",
                    "params": {"experiment_id": "exp_current", "research_cohort": "clean_discovery"},
                    "summary": {"data_version": "live", "universe_mode": "pit", "circ_mv_source": "archive_daily_basic"},
                },
                {
                    "name": "stale",
                    "source": "llm",
                    "params": {"experiment_id": "exp_current", "research_cohort": "clean_discovery"},
                    "summary": {"data_version": "old", "universe_mode": "pit", "circ_mv_source": "archive_daily_basic"},
                },
            ]

        def list_factors(self):
            return [
                {"name": "legacy", "status": "screened", "cohort": "legacy_snapshot_research"},
                {"name": "current", "status": "screened", "cohort": "clean_discovery"},
                {
                    "name": "stale",
                    "status": "screened",
                    "cohort": "clean_discovery",
                },
            ]

        def load_factor(self, name):
            return SimpleNamespace(compute=lambda _ctx: panel)

    svc = object.__new__(EvalService)
    svc.registry = _Registry()
    svc.clean_experiment = True
    svc.peer_experiment_id = "exp_current"
    svc._peer_cache = {}
    svc.trade_lag = lambda: 1
    svc._context = lambda: None
    svc._prepare_eval_panel = lambda raw: (raw, [])
    svc.data = SimpleNamespace(data_version=lambda: "live")

    peers = svc._peer_panels("new_factor", set(), statuses=("screened",))

    assert set(peers) == {"current"}
