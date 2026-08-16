from qfactor.agent.coldstart import (
    DSL_SEEDS,
    field_window_prior,
    is_cold_start,
    parent_count,
)
from qfactor.agent.diversity import active_skeleton_bans, keep_mechanism_coverage
from qfactor.agent.generator import (
    CandidateGenerator,
    _COMPOSE_UNARY,
    collect_fields_windows,
    llm_slot_plan,
)
from qfactor.agent.llm import LLMClient
from qfactor.agent.loop import graph_rounds_for_budget
from qfactor.dsl.validate import validate_expression


def test_graph_rounds_for_budget():
    assert graph_rounds_for_budget(3600) == 10
    assert graph_rounds_for_budget(70) == 1
    assert graph_rounds_for_budget(200) >= 2
    assert graph_rounds_for_budget(50) == 1


def _eligible_parent(status: str, **extra):
    row = {
        "status": status,
        "source": "llm",
        "params": {"research_cohort": "clean_discovery"},
        "summary": {
            "universe_mode": "pit",
            "circ_mv_source": "archive_daily_basic",
            "data_version": "live",
        },
    }
    row.update(extra)
    return row


def test_is_cold_start_threshold():
    seven = [_eligible_parent("screened")] * 7
    eight = [_eligible_parent("screened")] * 8
    mixed = [{"status": "draft"}] * 20 + [_eligible_parent("candidate")] * 4
    legacy = [
        {
            "status": "screened",
            "summary": {"universe_mode": "snapshot", "circ_mv_source": "estimated"},
        }
    ] * 137
    assert parent_count(seven) == 7
    assert is_cold_start(seven) is True
    assert is_cold_start(eight) is False
    assert is_cold_start(mixed) is True
    assert is_cold_start([]) is True
    assert parent_count(legacy) == 0
    assert is_cold_start(legacy) is True
    assert parent_count([{"status": "screened"}] * 20) == 0


def test_parent_count_collapses_window_shopped_skeletons():
    clones = [
        _eligible_parent("screened", expression="ma(amplitude,20)")
        for _ in range(8)
    ]
    assert parent_count(clones) == 1
    assert is_cold_start(clones) is True
    mixed_exprs = [
        "neg(roc(close_adj,5))",
        "ma(overnight,20)",
        "ma(div(abs(ret_1d),amount),20)",
        "std(ret_1d,20)",
        "ma(turnover_rate,20)",
        "ma(lower_shadow,20)",
        "div(vol,ma(vol,20))",
    ]
    mixed = clones[:1] + [
        _eligible_parent("screened", expression=expr) for expr in mixed_exprs
    ]
    assert parent_count(mixed) == 8
    assert is_cold_start(mixed) is False


def test_llm_slot_plan_cold_keeps_fresh_when_catalog_thick():
    plan = llm_slot_plan(
        8,
        unused_compose=80,
        n_usable=0,
        ratio=0.45,
        has_parents=True,
        cold_start=True,
        fresh_ratio=0.30,
    )
    assert plan["skip_llm"] is False
    assert plan["n_fresh"] >= 1
    assert plan["n_fresh"] + plan["n_mutate"] + plan["n_template"] == 8


def test_llm_slot_plan_hot_thin_catalog_splits_fresh_and_mutate():
    plan = llm_slot_plan(
        8,
        unused_compose=3,
        n_usable=4,
        ratio=0.45,
        has_parents=True,
        cold_start=False,
    )
    assert plan["skip_llm"] is False
    assert plan["n_fresh"] == 3
    assert plan["n_mutate"] == 1
    assert plan["n_crossover"] >= 3
    assert plan["n_fresh"] + plan["n_mutate"] + plan["n_crossover"] + plan["n_template"] == 8


def test_active_skeleton_bans_cold_skips_library_fsa_keeps_eligible_cap(monkeypatch):
    monkeypatch.setattr(
        "qfactor.agent.diversity.skeleton_keep_counts",
        lambda cfg=None: {"std(ret_1d,N)": 2},
    )
    monkeypatch.setattr(
        "qfactor.agent.diversity.library_diversity_index",
        lambda cfg=None: {"banned_skeletons": ["high_corr_skel"]},
    )
    assert active_skeleton_bans(max_per=2, extra=["extra_sk"], cold_start=True) == {
        "extra_sk"
    }
    existing = [
        _eligible_parent(
            "screened",
            expression="ma(amplitude,20)",
        )
        for _ in range(2)
    ]
    banned = active_skeleton_bans(
        max_per=2, extra=["extra_sk"], cold_start=True, existing=existing
    )
    assert "high_corr_skel" not in banned
    assert "extra_sk" in banned
    from qfactor.agent.diversity import expression_fingerprint

    assert expression_fingerprint("ma(amplitude,20)")["skeleton"] in banned


def test_decide_theme_cold_start_rotates_off_winning_field():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen.llm_cfg["llm_decide_theme"] = False
    existing = [
        _eligible_parent(
            "screened",
            mechanism="amplitude",
            expression="ma(amplitude,20)",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.016,
            },
        )
        for _ in range(3)
    ]
    assert is_cold_start(existing) is True
    coverage = keep_mechanism_coverage(existing)
    theme = gen.decide_theme(
        coverage,
        existing,
        recent_themes=["amplitude", "amplitude"],
    )
    assert theme != "amplitude"
    assert coverage["amplitude"] == 3


def test_cold_field_prior_skips_saturated_keep_family():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    existing = [
        _eligible_parent(
            "screened",
            mechanism="amplitude",
            expression="ma(amplitude,20)",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.016,
            },
        )
        for _ in range(3)
    ] + [
        _eligible_parent(
            "screened",
            mechanism="reversal",
            expression="neg(roc(close_adj,5))",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.012,
            },
        )
    ]
    assert gen._refresh_field_window_prior([], existing, cold=True, round_idx=0, every=20)
    assert "amplitude" not in gen._field_prior
    assert gen._field_prior.get("close_adj", 0) > 0


def test_field_window_prior_weights_overnight():
    existing = [
        {
            "expression": "ma(overnight,20)",
            "status": "candidate",
            "summary": {"rank_ic_mean": 0.04},
        },
        {
            "expression": "roc(close_adj,20)",
            "status": "draft",
            "summary": {"rank_ic_mean": 0.005},
        },
    ]
    lessons = [
        {
            "expression": "div(overnight,ma(amplitude,20))",
            "detail": {"rank_ic_mean": 0.03},
        }
    ]
    field_w, win_w = field_window_prior(lessons, existing)
    assert field_w["overnight"] > field_w.get("close_adj", 0)
    assert win_w.get(20, 0) > 0


def test_field_window_prior_hot_skips_blocked_and_prefers_resid():
    existing = [
        {
            "expression": "ma(amplitude,20)",
            "mechanism": "amplitude",
            "status": "candidate",
            "summary": {"rank_ic_mean": 0.08, "resid_ic_mean": 0.04},
        },
        {
            "expression": "div(vol,amplitude)",
            "mechanism": "liquidity",
            "status": "screened",
            "summary": {"rank_ic_mean": 0.001, "resid_ic_mean": 0.03},
        },
        {
            "expression": "ma(lower_shadow,20)",
            "mechanism": "shadow",
            "status": "screened",
            "summary": {"rank_ic_mean": 0.001, "resid_ic_mean": 0.02},
        },
    ]
    field_w, _ = field_window_prior(
        [],
        existing,
        blocked_mechanisms={"amplitude", "overnight"},
        blocked_fields={"amplitude", "overnight", "high", "low"},
        prefer_oos=True,
    )
    assert "amplitude" not in field_w
    assert field_w["vol"] > 0
    assert field_w["lower_shadow"] > 0


def test_hot_generate_does_not_boost_blocked_fields(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    monkeypatch.setattr(gen, "_unused_compose_count", lambda bans: 80)
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: [])
    existing = [
        {
            "mechanism": "amplitude",
            "status": "candidate",
            "expression": "ma(amplitude,20)",
            "summary": {"rank_ic_mean": 0.05, "resid_ic_mean": 0.04},
        },
        {
            "mechanism": "overnight",
            "status": "candidate",
            "expression": "ma(overnight,20)",
            "summary": {"rank_ic_mean": 0.03, "resid_ic_mean": 0.02},
        },
        {
            "mechanism": "shadow",
            "status": "screened",
            "expression": "ma(lower_shadow,20)",
            "summary": {"rank_ic_mean": 0.01, "resid_ic_mean": 0.015},
        },
    ]
    gen.generate_batch(n=1, existing=existing, theme="shadow")
    assert gen._field_prior.get("amplitude", 0) == 0
    assert gen._field_prior.get("overnight", 0) == 0
    assert gen._field_prior.get("lower_shadow", 0) > 0
    assert gen.last_stats["prior_refreshed"] is True


def test_prior_update_every_skips_rebuild(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    monkeypatch.setattr(gen, "_unused_compose_count", lambda bans: 80)
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: [])
    existing = [
        {
            "mechanism": "shadow",
            "status": "screened",
            "expression": "ma(lower_shadow,20)",
            "summary": {"resid_ic_mean": 0.02},
        },
        {
            "mechanism": "amplitude",
            "status": "candidate",
            "expression": "ma(amplitude,20)",
            "summary": {"resid_ic_mean": 0.04},
        },
    ]
    gen.generate_batch(n=1, existing=existing, round_idx=1)
    assert gen.last_stats["prior_refreshed"] is True
    gen._field_prior = {"sentinel": 1.0}
    gen.generate_batch(n=1, existing=existing, round_idx=5)
    assert gen.last_stats["prior_refreshed"] is False
    assert gen._field_prior == {"sentinel": 1.0}
    gen.generate_batch(n=1, existing=existing, round_idx=21)
    assert gen.last_stats["prior_refreshed"] is True
    assert "sentinel" not in gen._field_prior


def test_dsl_seeds_parse_and_validate():
    assert len(DSL_SEEDS) >= 8
    for seed in DSL_SEEDS:
        out = validate_expression(seed["expression"])
        assert out["ok"], (seed["name"], out.get("errors"))
        fields, windows = collect_fields_windows(seed["expression"])
        assert fields


def test_compose_unary_catalog_is_single_field():
    assert len(_COMPOSE_UNARY) >= 20
    for _mech, tmpl in _COMPOSE_UNARY:
        fields, _ = collect_fields_windows(tmpl.format(w=20, w2=60))
        assert len(fields) <= 1


def test_curriculum_compose_emits_unary():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._curriculum = True
    gen._field_prior = {}
    gen._window_prior = {}
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    cand = gen._compose_one("overnight", bans, {})
    assert cand is not None
    fields, _ = collect_fields_windows(cand["expression"])
    assert len(fields) <= 1


def test_generate_cold_does_not_skip_llm(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: True
    )
    monkeypatch.setattr(gen, "_unused_compose_count", lambda bans: 80)
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: [])
    monkeypatch.setattr(gen, "_llm_fresh_batch", lambda *a, **k: [])
    out = gen.generate_batch(n=4, theme="reversal")
    assert gen.last_stats["cold_start"] is True
    assert gen.last_stats["curriculum"] is True
    assert gen.last_stats["n_fresh"] >= 1
    assert gen.last_stats["llm_skipped"] is False
    assert len(out) == 4


def test_parent_pool_injects_dsl_seeds_when_cold(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: True
    )

    class _Reg:
        def list_factors(self):
            return []

        def load_spec(self, name):
            raise KeyError(name)

    monkeypatch.setattr("qfactor.factor.registry.FactorRegistry", lambda cfg=None: _Reg())
    pool = gen._parent_pool([])
    names = {p["name"] for p in pool}
    assert "seed_overnight_ma_20" in names
    assert "seed_amihud_20" in names
    assert all(p["status"] == "draft" for p in pool if p["name"].startswith("seed_"))


def test_hot_parent_pool_keeps_eligible_mechs_not_global_ic(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )

    class _Reg:
        def list_factors(self):
            return []

        def load_spec(self, name):
            raise KeyError(name)

    monkeypatch.setattr("qfactor.factor.registry.FactorRegistry", lambda cfg=None: _Reg())
    existing = [
        _eligible_parent(
            "candidate",
            name="amp_c",
            expression="ma(amplitude,20)",
            mechanism="amplitude",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.05,
                "resid_ic_mean": 0.04,
            },
        ),
        _eligible_parent(
            "candidate",
            name="liq_c",
            expression="ma(turnover_rate,20)",
            mechanism="liquidity",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.04,
                "resid_ic_mean": 0.03,
            },
        ),
    ] + [
        _eligible_parent(
            "screened",
            name=f"amp_s{i}",
            expression=f"std(amplitude,{5 + i})",
            mechanism="amplitude",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.03,
                "resid_ic_mean": 0.02,
            },
        )
        for i in range(12)
    ] + [
        _eligible_parent(
            "screened",
            name="rev_s",
            expression="neg(roc(close_adj,5))",
            mechanism="reversal",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.012,
                "resid_ic_mean": 0.01,
            },
        ),
        _eligible_parent(
            "screened",
            name="sh_s",
            expression="ma(upper_shadow,10)",
            mechanism="shadow",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.011,
                "resid_ic_mean": 0.01,
            },
        ),
        _eligible_parent(
            "screened",
            name="mom_amp_field",
            expression="ma(amplitude,20)",
            mechanism="momentum",
            summary={
                "universe_mode": "pit",
                "circ_mv_source": "archive_daily_basic",
                "data_version": "live",
                "rank_ic_mean": 0.02,
                "resid_ic_mean": 0.02,
            },
        ),
    ]
    pool = gen._parent_pool(existing)
    mechs = {str(p.get("mechanism")) for p in pool if p.get("status") == "screened"}
    assert "reversal" in mechs
    assert "shadow" in mechs
    assert "amplitude" not in mechs
    assert not any(p.get("name") == "mom_amp_field" for p in pool)


def test_parent_pool_drops_legacy_snapshot_rows(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start",
        lambda existing, cfg=None: True,
    )

    class _Reg:
        def list_factors(self):
            return []

        def load_spec(self, name):
            raise KeyError(name)

    monkeypatch.setattr("qfactor.factor.registry.FactorRegistry", lambda cfg=None: _Reg())
    existing = [
        {
            "name": "legacy_amp",
            "expression": "ma(amplitude,20)",
            "mechanism": "amplitude",
            "status": "screened",
            "source": "compose",
            "summary": {"universe_mode": "snapshot", "circ_mv_source": "estimated"},
        },
        _eligible_parent(
            "screened",
            name="clean_rev",
            expression="neg(roc(close_adj,5))",
            mechanism="reversal",
        ),
    ]
    pool = gen._parent_pool(existing)
    names = {p["name"] for p in pool}
    assert "legacy_amp" not in names
    assert "clean_rev" in names
    assert parent_count(existing) == 1
    assert is_cold_start(existing) is True
