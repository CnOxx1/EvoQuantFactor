from qfactor.agent.diversity import keep_mechanism_coverage
from qfactor.agent.generator import (
    CandidateGenerator,
    _CATALOG_SKELETONS,
    _COMPOSE_CATALOG,
    rebuild_compose_catalog,
    should_expand_compose_catalog,
    validate_compose_template,
)
from qfactor.agent.graph import _maybe_expand_catalog, _node_decide, _node_generate


def test_should_expand_compose_catalog_gates():
    # default unused_lt=0: refill on an interval, catalog thickness does not matter
    assert should_expand_compose_catalog(80, 0, None) is False
    assert should_expand_compose_catalog(80, 5, None) is False
    assert should_expand_compose_catalog(80, 20, None) is True
    assert should_expand_compose_catalog(0, 20, None) is True
    assert should_expand_compose_catalog(80, 15, 10) is False
    assert should_expand_compose_catalog(80, 30, 10) is True
    # unused_lt>0 remains an opt-in "only when empty" switch
    assert should_expand_compose_catalog(80, 30, 10, unused_lt=5) is False
    assert should_expand_compose_catalog(0, 30, 10, unused_lt=5) is True
    # empty catalog uses empty_every=5 instead of waiting 20 rounds
    assert should_expand_compose_catalog(0, 4, None) is False
    assert should_expand_compose_catalog(0, 5, None) is True
    assert should_expand_compose_catalog(0, 14, 10) is False
    assert should_expand_compose_catalog(0, 15, 10) is True


def test_validate_rejects_catalog_clone_and_unknown_mech():
    mech_ids = {"reversal", "shadow", "liquidity"}
    known = set(_CATALOG_SKELETONS)
    clone = next(tmpl for _m, tmpl in _COMPOSE_CATALOG if "{w}" in tmpl)
    _norm, err = validate_compose_template(
        "reversal", clone, mechanism_ids=mech_ids, known_skeletons=known
    )
    assert err == "duplicate_skeleton"
    _norm, err = validate_compose_template(
        "pe_ttm",
        "add(rank(vol),neg(rank(ret_1d)))",
        mechanism_ids=mech_ids,
        known_skeletons=known,
    )
    assert err == "unknown_mechanism"
    _norm, err = validate_compose_template(
        "shadow",
        "close.pct_change({w})",
        mechanism_ids=mech_ids,
        known_skeletons=known,
    )
    assert err
    ok, err = validate_compose_template(
        "shadow",
        "add(ma(upper_shadow,{w}),neg(rank(turnover_rate)))",
        mechanism_ids=mech_ids,
        known_skeletons=known,
    )
    assert err == ""
    assert ok is not None
    assert "{w}" in ok or "rank(upper_shadow)" in ok


def test_expand_merges_new_skeletons_only(monkeypatch, tmp_path):
    extra = tmp_path / "extra_templates.yaml"
    monkeypatch.setattr(
        "qfactor.agent.generator.extra_templates_path", lambda cfg=None: extra
    )

    class _Fake:
        enabled = True

        def require_enabled(self):
            return None

        def chat_json(self, _system, user):
            clone = next(tmpl for _m, tmpl in _COMPOSE_CATALOG)
            return {
                "templates": [
                    {"mechanism": "shadow", "tmpl": clone},
                    {"mechanism": "nope", "tmpl": "add(rank(vol),rank(amount))"},
                    {
                        "mechanism": "shadow",
                        "tmpl": "add(ma(upper_shadow,{w}),neg(rank(turnover_rate)))",
                    },
                    {
                        "mechanism": "liquidity",
                        "tmpl": "add(rank(vol),neg(ma(turnover_rate,{w})))",
                    },
                    {
                        "mechanism": "reversal",
                        "tmpl": "add(neg(roc(close_adj,{w})),rank(turnover_rate))",
                    },
                    {
                        "mechanism": "momentum",
                        "tmpl": "add(roc(close_adj,{w}),neg(rank(vol)))",
                    },
                    {
                        "mechanism": "volatility",
                        "tmpl": "add(std(ret_1d,{w}),neg(rank(turnover_rate)))",
                    },
                    {
                        "mechanism": "volume_price",
                        "tmpl": "add(rank(roc(close_adj,{w})),neg(rank(roc(vol,{w}))))",
                    },
                    {
                        "mechanism": "shadow",
                        "tmpl": "add(ma(upper_shadow,{w}),neg(ma(lower_shadow,{w})))",
                    },
                    {
                        "mechanism": "liquidity",
                        "tmpl": "add(div(vol,ma(vol,{w})),neg(rank(amount)))",
                    },
                ]
            }

    gen = CandidateGenerator(llm=_Fake())  # type: ignore[arg-type]
    import qfactor.agent.generator as gmod

    gmod.rebuild_compose_catalog(extra=[])
    before = len(gmod._COMPOSE_CATALOG)
    try:
        out = gen.expand_compose_catalog_via_llm(n_ask=12, max_accept=10, blocked={"amplitude"})
        assert out["attempted"] is True
        assert out["n_accepted"] <= 10
        assert out["n_accepted"] >= 8
        assert extra.exists()
        assert out["n_catalog_after"] == before + out["n_accepted"]
        reasons = {r["reason"] for r in out["rejected"]}
        assert "duplicate_skeleton" in reasons or "blocked_or_unknown_mechanism" in reasons
        again = gen.expand_compose_catalog_via_llm(n_ask=12, max_accept=10, blocked={"amplitude"})
        assert again["n_accepted"] == 0
    finally:
        gmod.rebuild_compose_catalog()


def test_maybe_expand_skips_cooldown_and_cold_start():
    class _Gen:
        llm_cfg = {
            "catalog_expand_every": 20,
            "catalog_expand_unused_lt": 0,
            "catalog_expand_max": 10,
        }

        def expand_compose_catalog_via_llm(self, **_k):
            raise AssertionError("LLM catalog expand must not run")

    class _Ctx:
        generator = _Gen()

    info, last = _maybe_expand_catalog(
        _Ctx(),  # type: ignore[arg-type]
        {"rounds_done": 5, "last_catalog_expand_round": None},
        {"unused_compose": 80, "cold_start": False},
    )
    assert info["attempted"] is False
    assert last is None

    info, last = _maybe_expand_catalog(
        _Ctx(),  # type: ignore[arg-type]
        {"rounds_done": 12, "last_catalog_expand_round": 10},
        {"unused_compose": 0, "cold_start": False},
    )
    assert info["attempted"] is False
    assert last == 10

    info, last = _maybe_expand_catalog(
        _Ctx(),  # type: ignore[arg-type]
        {"rounds_done": 40, "last_catalog_expand_round": None},
        {"unused_compose": 80, "cold_start": True},
    )
    assert info["attempted"] is False


def test_maybe_expand_runs_while_catalog_still_thick():
    calls: list[dict] = []

    class _Gen:
        llm_cfg = {
            "catalog_expand_every": 20,
            "catalog_expand_unused_lt": 0,
            "catalog_expand_max": 10,
        }

        def expand_compose_catalog_via_llm(self, **kwargs):
            calls.append(kwargs)
            return {"attempted": True, "n_accepted": 2, "ok": True}

    class _Ctx:
        generator = _Gen()

    info, last = _maybe_expand_catalog(
        _Ctx(),  # type: ignore[arg-type]
        {"rounds_done": 20, "last_catalog_expand_round": None},
        {"unused_compose": 80, "cold_start": False, "blocked_mechanisms": ["amplitude"]},
    )
    assert info["n_accepted"] == 2
    assert last == 20
    assert calls[0]["blocked"] == {"amplitude"}


def test_decide_node_uses_keep_inventory_not_hits(monkeypatch):
    captured: dict = {}

    class _Gen:
        def decide_theme(self, coverage, existing, **_k):
            captured["coverage"] = dict(coverage)
            return "reversal"

    class _Reg:
        def existing_summaries(self):
            return [
                {"mechanism": "liquidity", "status": "screened"},
                {"mechanism": "liquidity", "status": "screened"},
                {"mechanism": "amplitude", "status": "candidate"},
                {"mechanism": "reversal", "status": "draft"},
            ]

    class _Cfg:
        project = {"production": {"cold_start": {"disable_fsa": True}, "diversity": {}}}

    class _Ctx:
        cfg = _Cfg()
        generator = _Gen()
        registry = _Reg()

    monkeypatch.setattr("qfactor.agent.graph.library_diversity_index", lambda cfg=None: {"expr_hashes": []})
    monkeypatch.setattr("qfactor.agent.graph.active_skeleton_bans", lambda *a, **k: set())
    decide = _node_decide(_Ctx())  # type: ignore[arg-type]
    out = decide(
        {
            "mechanism_hits": {"reversal": 99, "liquidity": 1},
            "lessons": [],
            "recent_themes": [],
            "banned_hashes": [],
            "high_corr_skeletons": [],
            "rounds_done": 3,
        }
    )
    assert captured["coverage"] == {"liquidity": 2, "amplitude": 1}
    assert captured["coverage"] != {"reversal": 99, "liquidity": 1}
    assert out["round_theme"] == "reversal"
    assert out["rounds_done"] == 4


def test_generate_node_passes_keep_coverage_and_round_idx(monkeypatch):
    captured: dict = {}

    class _Gen:
        llm_cfg = {"llm_ratio": 0.45}
        last_stats = {
            "llm_skipped": True,
            "blocked_mechanisms": ["amplitude"],
            "keep_coverage": {"liquidity": 2, "amplitude": 1},
            "prior_refreshed": True,
            "curriculum": False,
        }

        def generate_batch(self, **kwargs):
            captured.update(kwargs)
            return [{"expression": "ma(vol,20)", "source": "compose", "name": "x"}]

    class _Reg:
        def existing_summaries(self):
            return [
                {"mechanism": "liquidity", "status": "screened"},
                {"mechanism": "liquidity", "status": "screened"},
                {"mechanism": "amplitude", "status": "candidate"},
            ]

    class _Ctx:
        generator = _Gen()
        registry = _Reg()

    generate = _node_generate(_Ctx())  # type: ignore[arg-type]
    out = generate(
        {
            "batch_size": 4,
            "round_theme": "reversal",
            "llm_ratio": 0.45,
            "banned_skeletons": [],
            "banned_hashes": [],
            "rounds_done": 20,
            "lessons": [],
        }
    )
    assert captured["round_idx"] == 20
    assert captured["coverage"] == keep_mechanism_coverage(_Reg().existing_summaries())
    assert captured["coverage"]["liquidity"] == 2
    assert "amplitude" in captured["coverage"]
    assert out["round_stats"]["keep_coverage"]["liquidity"] == 2
    assert out["round_stats"]["prior_refreshed"] is True
