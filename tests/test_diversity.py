from qfactor.agent.coldstart import collect_fields_windows
from qfactor.agent.diversity import (
    active_skeleton_bans,
    expression_fingerprint,
    is_banned_expression,
    keep_mechanism_coverage,
    pick_theme_with_lessons,
    saturated_skeletons,
    unique_factor_name,
)
from qfactor.agent.generator import CandidateGenerator, _COMPOSE_CATALOG
from qfactor.agent.llm import LLMClient
from qfactor.dsl.parser import skeleton, parse_expression


def test_unique_factor_name_not_bare():
    a = unique_factor_name("reversal", "llm")
    b = unique_factor_name("reversal", "llm")
    assert a != b
    assert a.startswith("reversal_")
    assert "reversal_5d" != a


def test_skeleton_blank_windows():
    a = parse_expression("neg(roc(close_adj,5))")
    b = parse_expression("neg(roc(close_adj,20))")
    assert skeleton(a) == skeleton(b)


def test_ban_duplicate_and_parent_skeleton():
    bans = {"hashes": set(), "skeletons": {"neg(roc(close_adj))"}}
    # note: skeleton uses N for numbers -> neg(roc(close_adj,N))? Check actual
    sk = expression_fingerprint("neg(roc(close_adj,5))")["skeleton"]
    bans = {"hashes": set(), "skeletons": {sk}}
    banned, why = is_banned_expression("neg(roc(close_adj,60))", bans)
    assert banned and why == "banned_skeleton"

    bans2 = {"hashes": set(), "skeletons": set()}
    parent = expression_fingerprint("neg(delta(close_adj,10))")["skeleton"]
    banned2, why2 = is_banned_expression(
        "neg(delta(close_adj,20))", bans2, parent_skeleton=parent
    )
    assert banned2 and why2 == "same_parent_skeleton"


def test_pick_theme_soft_switch():
    mechs = [{"id": "reversal"}, {"id": "overnight"}, {"id": "liquidity"}]
    lessons = [
        {"mechanism": "reversal", "reason": "weak_ic"},
        {"mechanism": "reversal", "reason": "weak_ic"},
        {"mechanism": "reversal", "reason": "weak_ic"},
    ]
    # forced still ok under 3? soft_switch_after default 3 means fails < 3 keeps forced
    # with 3 fails, soft switch
    theme = pick_theme_with_lessons(
        mechs, coverage={"overnight": 5, "liquidity": 0, "reversal": 2}, lessons=lessons, forced="reversal", soft_switch_after=3
    )
    assert theme != "reversal" or theme in {"overnight", "liquidity", "reversal"}
    # with 3 failures and soft_switch_after=3, forced is abandoned when fails >= soft_switch_after
    # pick_theme: if forced and fails < soft_switch_after return forced
    # fails=3, soft_switch_after=3 -> 3 < 3 is False -> soft switch
    assert theme in {"overnight", "liquidity"}


def test_pick_theme_hard_rotate():
    mechs = [{"id": "reversal"}, {"id": "overnight"}, {"id": "liquidity"}]
    theme = pick_theme_with_lessons(
        mechs,
        coverage={"reversal": 0, "overnight": 0, "liquidity": 0},
        lessons=[],
        recent_themes=["reversal", "overnight"],
        hard_rotate=True,
    )
    assert theme == "liquidity"


def test_pick_theme_hard_rotate_off_allows_recent():
    mechs = [{"id": "reversal"}, {"id": "overnight"}]
    theme = pick_theme_with_lessons(
        mechs,
        coverage={"reversal": 0, "overnight": 5},
        lessons=[],
        recent_themes=["reversal"],
        hard_rotate=False,
    )
    assert theme == "reversal"


def test_pick_theme_penalizes_usable_library_families():
    mechs = [{"id": "amplitude"}, {"id": "liquidity"}, {"id": "reversal"}]
    theme = pick_theme_with_lessons(
        mechs,
        coverage={"amplitude": 0, "liquidity": 0, "reversal": 0},
        lessons=[],
        usable_coverage={"amplitude": 3, "liquidity": 0, "reversal": 0},
        hard_rotate=False,
    )
    assert theme in {"liquidity", "reversal"}


def test_saturated_skeletons_caps_window_shopping(monkeypatch):
    monkeypatch.setattr(
        "qfactor.agent.diversity.skeleton_keep_counts",
        lambda cfg=None: {"std(ret_1d,N)": 2, "ma(overnight,N)": 1},
    )
    sat = saturated_skeletons(max_per=2)
    assert "std(ret_1d,N)" in sat
    assert "ma(overnight,N)" not in sat


def test_active_skeleton_bans_union_live_and_extra(monkeypatch):
    monkeypatch.setattr(
        "qfactor.agent.diversity.skeleton_keep_counts",
        lambda cfg=None: {"std(ret_1d,N)": 2, "ma(overnight,N)": 1},
    )
    monkeypatch.setattr(
        "qfactor.agent.diversity.library_diversity_index",
        lambda cfg=None: {"banned_skeletons": ["high_corr_skel"]},
    )
    active = active_skeleton_bans(max_per=2, extra=["extra_sk"])
    assert "std(ret_1d,N)" in active
    assert "high_corr_skel" in active
    assert "extra_sk" in active
    assert "ma(overnight,N)" not in active


def test_compose_catalog_has_many_unique_skeletons():
    skels: set[str] = set()
    for _mech, tmpl in _COMPOSE_CATALOG:
        expr = tmpl.format(w=20, w2=60)
        skels.add(skeleton(parse_expression(expr)))
    assert len(_COMPOSE_CATALOG) >= 80
    assert len(skels) >= 80
    assert len(skels) == len(_COMPOSE_CATALOG)


def test_compose_one_skips_banned_skeletons():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    got = []
    for _ in range(len(_COMPOSE_CATALOG)):
        cand = gen._compose_one(None, bans, {})
        if not cand:
            break
        fp = expression_fingerprint(cand["expression"])
        assert fp["skeleton"] not in bans["skeletons"]
        bans["skeletons"].add(fp["skeleton"])
        got.append(cand["expression"])
    assert len(got) >= 40


def test_structure_perturb_changes_skeleton():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    parent = {
        "expression": "mul(rank(roc(close_adj,5)),rank(roc(vol,5)))",
        "mechanism": "volume_price",
        "hypothesis": "base",
    }
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    pert = gen._perturb_structure(parent, bans)
    assert pert is not None
    assert expression_fingerprint(pert["expression"])["skeleton"] != expression_fingerprint(
        parent["expression"]
    )["skeleton"]
    parse_expression(pert["expression"])


def test_unused_compose_count_drops_banned():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    n0 = gen._unused_compose_count(bans)
    assert n0 >= 80
    for _, tmpl in _COMPOSE_CATALOG[:12]:
        bans["skeletons"].add(skeleton(parse_expression(tmpl.format(w=20, w2=60))))
    n1 = gen._unused_compose_count(bans)
    assert n1 <= n0 - 8


def test_factor_card_is_metrics_not_name_list():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    card = gen._factor_card(
        {
            "name": "should_not_appear",
            "expression": "max(overnight,60)",
            "mechanism": "overnight",
            "status": "candidate",
            "summary": {
                "train_rank_ic_mean": 0.037,
                "rank_ic_mean": 0.037,
                "eval_split": "train",
                "resid_ic_mean": 0.02,
                "max_corr": 0.4,
                "cost_adjusted_ls": 0.001,
                "icir": 0.34,
            },
        }
    )
    assert card is not None
    assert "name" not in card
    assert "holdout_ic" not in card
    assert "resid_ic" not in card
    assert "cost_ls" not in card
    assert card["expression"] == "max(overnight,60)"
    assert card["train_ic"] == 0.037
    assert card["max_corr"] == 0.4


def test_factor_card_drops_holdout_metrics_from_production_summary():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    card = gen._factor_card(
        {
            "expression": "ma(turnover_rate,20)",
            "mechanism": "liquidity",
            "status": "candidate",
            "summary": {
                "rank_ic_mean": 0.049,
                "train_rank_ic_mean": 0.021,
                "eval_split": "holdout",
                "resid_ic_mean": 0.03,
                "cost_adjusted_ls": 0.002,
                "icir": 0.18,
            },
        }
    )
    assert card is not None
    assert "holdout_ic" not in card
    assert card["train_ic"] == 0.021
    assert "resid_ic" not in card
    assert "cost_ls" not in card


def test_mutate_failure_modes_omit_holdout_numbers(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))

    class _Reg:
        def list_factors(self):
            return [
                {
                    "name": "liq_c",
                    "status": "candidate",
                    "summary": {
                        "eval_split": "holdout",
                        "resid_ic_mean": 0.03,
                        "cost_adjusted_ls": -0.01,
                        "max_corr": 0.8,
                    },
                }
            ]

        def load_spec(self, name):
            from types import SimpleNamespace

            return SimpleNamespace(
                expression="div(abs(ret_1d),ma(turnover_rate,60))",
                mechanism="liquidity",
            )

    monkeypatch.setattr("qfactor.factor.registry.FactorRegistry", lambda cfg=None: _Reg())
    modes = gen._mutate_failure_modes([])
    blob = " ".join(modes)
    assert "resid" not in blob
    assert "cost_ls" not in blob
    assert "-0.01" not in blob
    assert "0.03" not in blob


def test_generate_skips_llm_while_catalog_thick_without_usable(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: [])
    monkeypatch.setattr(gen, "_unused_compose_count", lambda bans: 80)
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    out = gen.generate_batch(n=4, theme="reversal")
    assert gen.last_stats["llm_skipped"] is True
    assert gen.last_stats["force_library_mutate"] is False
    assert gen.last_stats["n_llm"] == 0
    assert gen.last_stats["n_fresh"] == 0
    assert len(out) == 4


def test_llm_slot_plan_forces_mutate_when_library_exists():
    from qfactor.agent.generator import llm_slot_plan

    skipped = llm_slot_plan(
        4, unused_compose=80, n_usable=0, ratio=0.45, has_parents=False
    )
    assert skipped["skip_llm"] is True
    assert skipped["n_mutate"] == 0

    compose_only = llm_slot_plan(
        4, unused_compose=80, n_usable=4, ratio=0.45, has_parents=True, library_mutate_slots=0
    )
    assert compose_only["skip_llm"] is True
    assert compose_only["n_mutate"] == 0
    assert compose_only["n_template"] == 4

    forced = llm_slot_plan(
        4,
        unused_compose=80,
        n_usable=4,
        ratio=0.45,
        has_parents=True,
        library_mutate_slots=2,
    )
    assert forced["skip_llm"] is False
    assert forced["force_library_mutate"] is True
    assert forced["n_mutate"] == 2
    assert forced["n_fresh"] == 0
    assert forced["n_template"] == 2

    thin = llm_slot_plan(
        8, unused_compose=3, n_usable=4, ratio=0.45, has_parents=True
    )
    assert thin["skip_llm"] is False
    assert thin["n_fresh"] >= 1
    assert thin["n_mutate"] >= 1


def test_validate_idea_rejects_unknown_fields():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    mech = {"id": "shadow", "desc": "影线结构"}
    bad = gen._validate_idea(
        {
            "claim": "something long enough",
            "why_t1": "T+1 ok",
            "fields": ["pe_ttm"],
            "not_like": "not overnight std ratio",
        },
        mech,
    )
    assert bad is None
    ok = gen._validate_idea(
        {
            "claim": "长上影且高换手未来5日收益偏低",
            "why_t1": "T日收盘后可见",
            "fields": ["upper_shadow", "turnover_rate"],
            "not_like": "不是隔夜/振幅的std比",
            "mechanism": "shadow",
        },
        mech,
    )
    assert ok is not None
    assert ok["fields"] == ["upper_shadow", "turnover_rate"]


def test_generate_forces_idea_then_dsl_when_candidates_exist(monkeypatch):
    import json

    from qfactor.agent.diversity import expression_fingerprint

    calls: list[str] = []

    class _Fake:
        enabled = True
        n = 0

        def require_enabled(self):
            return None

        def chat_json(self, _system, user):
            payload = json.loads(user)
            calls.append(str(payload.get("task")))
            if payload.get("task") == "ideas":
                _Fake.n += 1
                if _Fake.n > 1:
                    return {"ideas": []}
                return {
                    "ideas": [
                        {
                            "claim": "长上影且高换手的股票未来5日收益偏低",
                            "why_t1": "T日K线与换手收盘后可见，T+1可交易",
                            "fields": ["upper_shadow", "turnover_rate"],
                            "not_like": "不是隔夜对振幅的标准差比",
                            "mechanism": "shadow",
                        }
                    ]
                }
            assert payload.get("task") == "compile"
            assert payload.get("ideas")
            return {
                "candidates": [
                    {
                        "expression": "add(rank(upper_shadow),neg(rank(turnover_rate)))",
                        "mechanism": "shadow",
                    }
                ]
            }

    gen = CandidateGenerator(llm=_Fake())  # type: ignore[arg-type]
    gen.llm_cfg["llm_library_mutate_slots"] = 2
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    monkeypatch.setattr(gen, "_unused_compose_count", lambda bans: 80)
    parents = [
        {
            "expression": "ma(overnight,20)",
            "mechanism": "overnight",
            "status": "candidate",
            "summary": {
                "rank_ic_mean": 0.04,
                "resid_ic_mean": 0.04,
                "max_corr": 0.1,
                "cost_adjusted_ls": 0.001,
            },
        },
        {
            "expression": "div(ma(upper_shadow,10),ma(turnover_rate,10))",
            "mechanism": "shadow",
            "status": "screened",
            "summary": {
                "rank_ic_mean": 0.02,
                "resid_ic_mean": 0.02,
                "max_corr": 0.2,
                "cost_adjusted_ls": 0.001,
            },
        },
    ]
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: parents)
    out = gen.generate_batch(n=4, theme="reversal", existing=parents)
    assert calls[0] == "ideas"
    assert "compile" in calls
    assert gen.last_stats["force_library_mutate"] is True
    assert gen.last_stats["llm_skipped"] is False
    assert gen.last_stats["n_mutate"] == 2
    assert gen.last_stats["n_fresh"] == 0
    mutated = [c for c in out if c.get("source") == "llm_mutate"]
    assert mutated
    sk = expression_fingerprint(mutated[0]["expression"])["skeleton"]
    parent_sks = {expression_fingerprint(p["expression"])["skeleton"] for p in parents}
    assert sk not in parent_sks
    assert mutated[0].get("idea")


def test_compile_drops_invalid_expression():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))

    class _Fake:
        enabled = True

        def require_enabled(self):
            return None

        def chat_json(self, _system, user):
            return {
                "candidates": [
                    {"expression": "close.pct_change(5)"},
                    {"expression": "add(rank(upper_shadow),neg(rank(turnover_rate)))"},
                ]
            }

    gen.llm = _Fake()  # type: ignore[assignment]
    mech = {"id": "shadow", "desc": "影线结构"}
    ideas = [
        {
            "claim": "长上影且高换手的股票未来5日收益偏低",
            "why_t1": "T日可见",
            "fields": ["upper_shadow", "turnover_rate"],
            "not_like": "不是隔夜振幅比",
            "mechanism": "shadow",
        }
    ]
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    out = gen._llm_compile_ideas(mech, ideas, [], bans)
    assert len(out) == 1
    assert "pct_change" not in out[0]["expression"]


def test_llm_slot_plan_empty_catalog_fresh_crossover_mutate():
    from qfactor.agent.generator import llm_slot_plan

    plan = llm_slot_plan(
        8, unused_compose=0, n_usable=4, ratio=0.45, has_parents=True
    )
    assert plan["n_fresh"] == 3
    assert plan["n_mutate"] == 1
    assert plan["n_crossover"] >= 3
    assert plan["n_template"] == 0
    assert plan["n_fresh"] + plan["n_crossover"] + plan["n_mutate"] + plan["n_template"] == 8


def test_pick_theme_hard_excludes_families_with_candidates():
    mechs = [
        {"id": "amplitude"},
        {"id": "overnight"},
        {"id": "liquidity"},
        {"id": "shadow"},
    ]
    theme = pick_theme_with_lessons(
        mechs,
        coverage={},
        lessons=[],
        usable_coverage={"amplitude": 3, "overnight": 1},
        hard_rotate=False,
    )
    assert theme in {"liquidity", "shadow"}


def test_priority_templates_are_new_skeletons():
    import qfactor.agent.generator as gmod
    from qfactor.dsl.validate import validate_expression

    gmod.rebuild_compose_catalog(extra=[])
    try:
        assert len(gmod._PRIORITY_SPECS) >= 15
        assert len(gmod._COMPOSE_PRIORITY) >= 10
        pri_tmpls = {t for _m, t in gmod._PRIORITY_SPECS}
        old_tmpls = {t for _m, t in gmod._COMPOSE_CATALOG if t not in pri_tmpls}
        old_skels = {skeleton(parse_expression(t.format(w=20, w2=60))) for t in old_tmpls}
        for _mech, tmpl in gmod._COMPOSE_PRIORITY:
            expr = tmpl.format(w=20, w2=60)
            v = validate_expression(expr)
            assert v["ok"], (tmpl, v.get("errors"))
            assert skeleton(parse_expression(expr)) not in old_skels
    finally:
        gmod.rebuild_compose_catalog()


def test_crossover_different_mechanisms_new_skeleton():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    parents = [
        {
            "expression": "mul(rank(turnover_rate),neg(roc(close_adj,20)))",
            "mechanism": "reversal",
        },
        {
            "expression": "div(ma(upper_shadow,10),ma(turnover_rate,10))",
            "mechanism": "shadow",
        },
    ]
    cand = gen._crossover_one(parents, bans, theme="reversal")
    assert cand is not None
    assert cand["source"] == "crossover"
    sk = expression_fingerprint(cand["expression"])["skeleton"]
    assert sk != expression_fingerprint(parents[0]["expression"])["skeleton"]
    assert sk != expression_fingerprint(parents[1]["expression"])["skeleton"]


def test_crossover_same_mechanism_rejected():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    parents = [
        {"expression": "neg(roc(close_adj,20))", "mechanism": "reversal"},
        {"expression": "neg(ma(ret_1d,10))", "mechanism": "reversal"},
    ]
    assert gen._crossover_one(parents, bans) is None


def test_fresh_empty_thin_catalog_does_not_compose(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    monkeypatch.setattr(gen, "_unused_compose_count", lambda bans: 0)
    monkeypatch.setattr(gen, "_llm_fresh_batch", lambda *a, **k: [])
    monkeypatch.setattr(gen, "_llm_mutate_batch", lambda *a, **k: [])
    parents = [
        {
            "expression": "mul(rank(turnover_rate),neg(roc(close_adj,20)))",
            "mechanism": "reversal",
            "status": "screened",
            "summary": {"resid_ic_mean": 0.02, "rank_ic_mean": 0.03},
        },
        {
            "expression": "div(ma(upper_shadow,10),ma(turnover_rate,10))",
            "mechanism": "shadow",
            "status": "screened",
            "summary": {"resid_ic_mean": 0.01, "rank_ic_mean": 0.02},
        },
    ]
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: parents)
    out = gen.generate_batch(
        n=8,
        theme="reversal",
        existing=[
            {**parents[0], "status": "screened"},
            {**parents[1], "status": "screened"},
        ],
    )
    assert gen.last_stats["n_fresh"] == 3
    assert gen.last_stats["n_crossover"] >= 3
    assert all(c.get("source") != "compose" for c in out)
    assert any(c.get("source") == "crossover" for c in out)


def test_decide_theme_skips_volume_price_when_liquidity_blocked(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen.llm_cfg["llm_decide_theme"] = False
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    existing = [
        {"mechanism": "amplitude", "status": "candidate"},
        {"mechanism": "liquidity", "status": "candidate"},
        {"mechanism": "overnight", "status": "candidate"},
        {"mechanism": "shadow", "status": "screened"},
    ]
    theme = gen.decide_theme({}, existing)
    assert theme not in {"amplitude", "liquidity", "overnight", "volume_price"}


def test_field_viable_drops_volume_price_without_vol_fields():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"liquidity"}
    kept = {m["id"] for m in gen._field_viable_mechanisms(gen.mechanisms)}
    assert "volume_price" not in kept
    assert "shadow" in kept
    assert "reversal" in kept


def test_decide_theme_skips_amplitude_and_overnight(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen.llm_cfg["llm_decide_theme"] = False
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    existing = [
        {"mechanism": "amplitude", "status": "candidate"},
        {"mechanism": "amplitude", "status": "candidate"},
        {"mechanism": "overnight", "status": "candidate"},
        {"mechanism": "shadow", "status": "screened"},
    ]
    theme = gen.decide_theme({}, existing)
    assert theme not in {"amplitude", "overnight"}


def test_keep_mechanism_coverage_counts_keep_not_draft():
    existing = [
        {"mechanism": "liquidity", "status": "screened"},
        {"mechanism": "liquidity", "status": "screened"},
        {"mechanism": "reversal", "status": "draft"},
        {"mechanism": "amplitude", "status": "candidate"},
    ]
    assert keep_mechanism_coverage(existing) == {"liquidity": 2, "amplitude": 1}


def test_pick_theme_prefers_low_keep_inventory():
    mechs = [{"id": "liquidity"}, {"id": "reversal"}, {"id": "shadow"}]
    theme = pick_theme_with_lessons(
        mechs,
        coverage={"liquidity": 24, "reversal": 6, "shadow": 19},
        lessons=[],
        usable_coverage={},
        hard_rotate=False,
    )
    assert theme == "reversal"


def test_decide_theme_uses_keep_coverage_not_generation_hits(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen.llm_cfg["llm_decide_theme"] = False
    monkeypatch.setattr(
        "qfactor.agent.generator.is_cold_start", lambda existing, cfg=None: False
    )
    existing = [
        {"mechanism": "amplitude", "status": "candidate"},
        {"mechanism": "overnight", "status": "candidate"},
        {"mechanism": "reversal", "status": "screened"},
        {"mechanism": "liquidity", "status": "screened"},
    ]
    theme = gen.decide_theme(
        {
            "liquidity": 24,
            "shadow": 19,
            "amplitude": 16,
            "overnight": 10,
            "volume_price": 10,
            "volatility": 8,
            "momentum": 7,
            "reversal": 1,
        },
        existing,
    )
    assert theme == "reversal"


def test_unused_compose_count_skips_blocked_mechanisms():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    gen._blocked_mechs = set()
    n_all = gen._unused_compose_count(bans)
    gen._blocked_mechs = {"amplitude", "overnight", "liquidity"}
    n_elig = gen._unused_compose_count(bans)
    assert n_elig < n_all


def test_crossover_skips_blocked_parents_and_fields():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"amplitude", "overnight"}
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    parents = [
        {"expression": "ma(amplitude,20)", "mechanism": "amplitude"},
        {"expression": "ma(overnight,20)", "mechanism": "overnight"},
        {
            "expression": "mul(rank(turnover_rate),neg(roc(close_adj,20)))",
            "mechanism": "reversal",
        },
        {
            "expression": "div(ma(upper_shadow,10),ma(turnover_rate,10))",
            "mechanism": "shadow",
        },
    ]
    cand = gen._crossover_one(parents, bans, theme="reversal")
    assert cand is not None
    fields, _ = collect_fields_windows(cand["expression"])
    assert "amplitude" not in fields
    assert "overnight" not in fields
    assert cand["mechanism"] not in {"amplitude", "overnight"}


def test_crossover_blocked_only_parents_returns_none():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"amplitude", "overnight"}
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    parents = [
        {"expression": "ma(amplitude,20)", "mechanism": "amplitude"},
        {"expression": "ma(overnight,20)", "mechanism": "overnight"},
    ]
    assert gen._crossover_one(parents, bans) is None


def test_llm_item_named_by_expression_mechanism():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    mech = next(m for m in gen.mechanisms if m["id"] == "momentum")
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    out = gen._normalize_llm_item(
        {
            "expression": "div(ma(turnover_rate,20),ma(abs(ret_1d),20))",
            "mechanism": "liquidity",
            "hypothesis": "amihud inverse",
        },
        mech,
        "llm",
        bans,
    )
    assert out is not None
    assert out["mechanism"] == "liquidity"
    assert out["name"].startswith("liquidity_")


def test_llm_item_drops_blocked_fields():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"overnight"}
    mech = next(m for m in gen.mechanisms if m["id"] == "momentum")
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    out = gen._normalize_llm_item(
        {"expression": "ma(overnight,20)", "mechanism": "momentum"},
        mech,
        "llm",
        bans,
    )
    assert out is None


def test_validate_idea_rejects_blocked_fields():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"liquidity"}
    mech = {"id": "shadow", "desc": "影线结构"}
    bad = gen._validate_idea(
        {
            "claim": "长上影且高换手未来5日收益偏低",
            "why_t1": "T日收盘后可见",
            "fields": ["upper_shadow", "turnover_rate"],
            "not_like": "不是隔夜/振幅的std比",
            "mechanism": "shadow",
        },
        mech,
    )
    assert bad is None
    ok = gen._validate_idea(
        {
            "claim": "长上影相对下影扩张后收益偏低",
            "why_t1": "T日收盘后可见",
            "fields": ["upper_shadow", "lower_shadow"],
            "not_like": "不是隔夜/振幅的std比",
            "mechanism": "shadow",
        },
        mech,
    )
    assert ok is not None
    assert ok["fields"] == ["upper_shadow", "lower_shadow"]


def test_crossover_trees_swaps_inner_unaries():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"amplitude", "liquidity", "overnight"}
    child = gen._crossover_trees(
        "std(roc(upper_shadow,10),5)",
        "div(std(close_adj,10), std(close_adj,20))",
    )
    assert child is not None
    assert child not in {
        "std(roc(upper_shadow,10),5)",
        "div(std(close_adj,10), std(close_adj,20))",
    }
    fields, _ = collect_fields_windows(child)
    assert not fields & {
        "amount",
        "amplitude",
        "high",
        "low",
        "overnight",
        "turnover_rate",
        "vol",
    }


def test_crossover_shallow_keep_parents_not_none():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"amplitude", "liquidity", "overnight"}
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    parents = [
        {"expression": "std(roc(upper_shadow,10),5)", "mechanism": "shadow"},
        {
            "expression": "div(std(close_adj,10), std(close_adj,20))",
            "mechanism": "volatility",
        },
        {"expression": "ma(roc(ret_1d,20),10)", "mechanism": "reversal"},
        {"expression": "std(roc(open,20),10)", "mechanism": "momentum"},
    ]
    cand = gen._crossover_one(parents, bans, theme="volatility")
    assert cand is not None
    assert cand["source"] == "crossover"
    fields, _ = collect_fields_windows(cand["expression"])
    assert not fields & {
        "amount",
        "amplitude",
        "high",
        "low",
        "overnight",
        "turnover_rate",
        "vol",
    }


def test_validate_idea_requires_two_fields_when_blocked():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"liquidity"}
    mech = {"id": "momentum", "desc": "动量"}
    bad = gen._validate_idea(
        {
            "claim": "收盘动量高的股票未来5日收益偏低",
            "why_t1": "T日收盘可见",
            "fields": ["close_adj"],
            "not_like": "不是隔夜振幅比",
            "mechanism": "momentum",
        },
        mech,
    )
    assert bad is None


def test_compile_retries_once_after_blocked_catalog_drop():
    import json

    class _Fake:
        enabled = True
        n = 0

        def require_enabled(self):
            return None

        def chat_json(self, _system, user):
            payload = json.loads(user)
            _Fake.n += 1
            if "上一轮" in str(payload.get("instruction") or ""):
                return {
                    "candidates": [
                        {
                            "expression": "div(roc(upper_shadow,10),std(close_adj,20))",
                            "mechanism": "shadow",
                        }
                    ]
                }
            return {
                "candidates": [
                    {"expression": "ma(turnover_rate,20)", "mechanism": "liquidity"}
                ]
            }

    gen = CandidateGenerator(llm=_Fake())  # type: ignore[arg-type]
    gen._blocked_mechs = {"amplitude", "liquidity", "overnight"}
    mech = next(m for m in gen.mechanisms if m["id"] == "shadow")
    ideas = [
        {
            "claim": "长上影相对价格波动扩张后收益偏低",
            "why_t1": "T日收盘可见",
            "fields": ["upper_shadow", "close_adj"],
            "not_like": "不是隔夜振幅比",
            "mechanism": "shadow",
        }
    ]
    out = gen._llm_compile_ideas(
        mech,
        ideas,
        [{"skeleton": "ma(upper_shadow,N)"}],
        {"hashes": set(), "skeletons": set()},
        source="llm",
    )
    assert _Fake.n == 2
    assert len(out) == 1
    assert "turnover_rate" not in out[0]["expression"]


def test_unadjusted_close_always_blocked():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = set()
    assert "close" not in gen._allowed_field_set()
    assert "close_adj" in gen._allowed_field_set()
    assert gen._expr_has_blocked_fields("roc(close,20)")
    assert not gen._expr_has_blocked_fields("roc(close_adj,20)")
    mech = {"id": "momentum", "desc": "动量"}
    bad = gen._validate_idea(
        {
            "claim": "未复权收盘涨的股票未来5日收益偏低",
            "why_t1": "T日收盘可见",
            "fields": ["close", "open"],
            "not_like": "不是隔夜振幅比",
            "mechanism": "momentum",
        },
        mech,
    )
    assert bad is None


def test_from_hint_skips_blocked_turnover_and_close():
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    gen._blocked_mechs = {"liquidity"}
    mech = next(m for m in gen.mechanisms if m["id"] == "reversal")
    bans: dict[str, set[str]] = {"hashes": set(), "skeletons": set()}
    cand = gen._from_hint(mech, bans)
    assert cand is not None
    fields, _ = collect_fields_windows(cand["expression"])
    assert "turnover_rate" not in fields
    assert "vol" not in fields
    assert "close" not in fields


