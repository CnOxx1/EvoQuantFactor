from types import SimpleNamespace

from qfactor.agent.graph import _eligible_research_library, _node_persist
from qfactor.factor.cohort import classify_research_cohort


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


def test_clean_experiment_uses_seeds_and_current_experiment_only():
    rows = [
        {"name": "seed", "source": "seed", "parent_eligible": True},
        {
            "name": "legacy",
            "source": "compose",
            "parent_eligible": False,
        },
        {
            "name": "current",
            "source": "llm",
            "parent_eligible": True,
            "params": {"experiment_id": "exp_current"},
        },
        {
            "name": "other_clean",
            "source": "llm",
            "parent_eligible": True,
            "params": {"experiment_id": "exp_other"},
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
