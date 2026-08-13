from qfactor.agent.diversity import (
    active_skeleton_bans,
    expression_fingerprint,
    is_banned_expression,
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
                "rank_ic_mean": 0.037,
                "resid_ic_mean": 0.02,
                "max_corr": 0.4,
                "cost_adjusted_ls": 0.001,
                "icir": 0.34,
            },
        }
    )
    assert card is not None
    assert "name" not in card
    assert card["expression"] == "max(overnight,60)"
    assert card["holdout_ic"] == 0.037
    assert card["resid_ic"] == 0.02
    assert card["cost_ls"] == 0.001


def test_generate_skips_llm_while_catalog_thick_without_usable(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: [])
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

    forced = llm_slot_plan(
        4, unused_compose=80, n_usable=4, ratio=0.45, has_parents=True
    )
    assert forced["skip_llm"] is False
    assert forced["force_library_mutate"] is True
    assert forced["n_mutate"] == 2
    assert forced["n_fresh"] == 0
    assert forced["n_template"] == 2


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
                        "expression": "div(ma(upper_shadow,10),ma(turnover_rate,10))",
                        "mechanism": "shadow",
                    }
                ]
            }

    gen = CandidateGenerator(llm=_Fake())  # type: ignore[arg-type]
    parents = [
        {
            "expression": "div(std(high,10),std(low,10))",
            "mechanism": "amplitude",
            "status": "candidate",
            "summary": {
                "rank_ic_mean": 0.04,
                "resid_ic_mean": 0.04,
                "max_corr": 0.1,
                "cost_adjusted_ls": 0.001,
            },
        }
    ]
    monkeypatch.setattr(gen, "_parent_pool", lambda existing: parents)
    out = gen.generate_batch(n=4, theme="reversal")
    assert calls[0] == "ideas"
    assert "compile" in calls
    assert gen.last_stats["force_library_mutate"] is True
    assert gen.last_stats["llm_skipped"] is False
    assert gen.last_stats["n_mutate"] == 2
    assert gen.last_stats["n_fresh"] == 0
    mutated = [c for c in out if c.get("source") == "llm_mutate"]
    assert mutated
    sk = expression_fingerprint(mutated[0]["expression"])["skeleton"]
    parent_sk = expression_fingerprint(parents[0]["expression"])["skeleton"]
    assert sk != parent_sk
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
                    {"expression": "div(ma(upper_shadow,10),ma(turnover_rate,10))"},
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

