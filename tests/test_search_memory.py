from pathlib import Path

from qfactor.agent.diversity import expression_fingerprint, pick_theme_with_lessons
from qfactor.agent.generator import CandidateGenerator
from qfactor.agent.llm import LLMClient
from qfactor.agent.search_memory import (
    SearchMemory,
    collapse_same_skeleton,
    compact_reject_trace,
    failed_checks_from_report,
    pareto_select_parents,
    seed_clean_checkpoint,
)


def _parent(name, expr, mechanism, *, ic, resid=0.0, corr=0.0, years=3, status="screened"):
    return {
        "name": name,
        "expression": expr,
        "mechanism": mechanism,
        "status": status,
        "summary": {
            "rank_ic_mean": ic,
            "resid_ic_mean": resid,
            "max_corr": corr,
            "years_consistent": years >= 3,
            "years": {"dominant_years": years, "n_years": 6},
        },
    }


def test_failed_checks_from_report():
    assert failed_checks_from_report(
        {"gate": {"checks": {"abs_rank_ic": True, "max_corr": False, "years": False}}}
    ) == ["max_corr", "years"]
    assert failed_checks_from_report({}) == []


def test_compact_reject_trace_omits_resid():
    note = compact_reject_trace(
        {
            "reason": "high_corr",
            "expression": "div(amplitude,ma(amplitude,20))",
            "detail": {
                "skeleton": "div(amplitude,ma(amplitude,N))",
                "failed_checks": ["max_corr"],
                "max_corr": 0.97,
                "rank_ic_mean": 0.011,
                "resid_ic_mean": 0.03,
            },
        }
    )
    assert note is not None
    assert "failed_checks=max_corr" in note
    assert "corr=0.97" in note
    assert "resid" not in note
    assert "0.03" not in note


def test_search_memory_persists_hash_not_weak_ic_skeleton(tmp_path: Path):
    mem = SearchMemory(data_version="dv1", path=tmp_path / "search_memory.json")
    expr = "std(amplitude,20)"
    fp = expression_fingerprint(expr)
    mem.record_reject(
        expression=expr,
        mechanism="amplitude",
        reason="weak_ic",
        stage="cheap_ic",
        detail={"rank_ic_mean": 0.006, "cheap": True, "failed_checks": ["cheap_ic"]},
    )
    assert fp["expr_hash"] in mem.failed_hashes()
    assert fp["skeleton"] not in mem.failed_skeletons()
    mem.save()

    class _Cfg:
        def path(self, key):
            assert key == "runs"
            return tmp_path

    loaded = SearchMemory.load(_Cfg(), data_version="dv1")
    assert fp["expr_hash"] in loaded.failed_hashes()
    assert fp["skeleton"] not in loaded.failed_skeletons()
    stale = SearchMemory.load(_Cfg(), data_version="dv2")
    assert stale.failed_hashes() == []
    assert stale.traces() == []


def test_search_memory_high_corr_bans_skeleton(tmp_path: Path):
    mem = SearchMemory(data_version="dv1", path=tmp_path / "search_memory.json")
    expr = "div(amplitude,ma(amplitude,20))"
    fp = expression_fingerprint(expr)
    mem.record_reject(
        expression=expr,
        mechanism="amplitude",
        reason="high_corr",
        stage="research_gate",
        detail={"max_corr": 0.97, "failed_checks": ["max_corr"], "rank_ic_mean": 0.011},
    )
    assert fp["expr_hash"] in mem.failed_hashes()
    assert fp["skeleton"] in mem.failed_skeletons()
    seeded = seed_clean_checkpoint(mem)
    assert fp["expr_hash"] in seeded["banned_hashes"]
    assert fp["skeleton"] in seeded["banned_skeletons"]
    assert seeded["recent_themes"] == []
    lessons = seeded["lessons"]
    assert lessons and lessons[0]["detail"]["skip_prior"] is True
    assert "max_corr" in lessons[0]["detail"]["failed_checks"]


def test_search_memory_recent_themes_roundtrip(tmp_path: Path):
    mem = SearchMemory(data_version="dv1", path=tmp_path / "search_memory.json")
    mem.set_recent_themes(["amplitude", "liquidity"])
    mem.save()

    class _Cfg:
        def path(self, key):
            return tmp_path

    loaded = SearchMemory.load(_Cfg(), data_version="dv1")
    assert loaded.recent_themes() == ["amplitude", "liquidity"]
    seeded = seed_clean_checkpoint(loaded)
    theme = pick_theme_with_lessons(
        [{"id": "amplitude"}, {"id": "liquidity"}, {"id": "reversal"}],
        coverage={},
        lessons=[],
        recent_themes=seeded["recent_themes"],
        hard_rotate=True,
    )
    assert theme == "reversal"


def test_collapse_same_skeleton_keeps_best_residual():
    rows = [
        _parent("a20", "ma(amplitude,20)", "amplitude", ic=0.016, resid=0.004),
        _parent("a40", "ma(amplitude,40)", "amplitude", ic=0.014, resid=0.009),
        _parent("liq", "ma(turnover_rate,20)", "liquidity", ic=0.014, resid=0.008),
    ]
    out = collapse_same_skeleton(rows)
    names = {r["name"] for r in out}
    assert "liq" in names
    assert ("a20" in names) ^ ("a40" in names)
    kept_amp = next(r for r in out if r["mechanism"] == "amplitude")
    assert kept_amp["name"] == "a40"


def test_pareto_keeps_orthogonal_axes_not_family_quota():
    pool = [
        _parent("amp_ic", "ma(amplitude,20)", "amplitude", ic=0.020, resid=0.004, corr=0.6, years=4),
        _parent("amp_resid", "std(amplitude,20)", "amplitude", ic=0.012, resid=0.011, corr=0.4, years=3),
        _parent("liq", "neg(ma(div(amount,turnover_rate),10))", "liquidity", ic=0.014, resid=0.008, corr=0.2, years=5),
        _parent("rev_years", "neg(roc(close_adj,5))", "reversal", ic=0.011, resid=0.003, corr=0.1, years=6),
        _parent("clone", "ma(amplitude,40)", "amplitude", ic=0.019, resid=0.003, corr=0.9, years=3),
    ]
    picked = pareto_select_parents(pool, limit=4)
    names = {p["name"] for p in picked}
    assert "amp_ic" in names
    assert "amp_resid" in names
    assert "liq" in names
    assert "clone" not in names
    mechs = {p["mechanism"] for p in picked}
    assert "amplitude" in mechs
    assert "liquidity" in mechs


def test_search_parents_may_use_amplitude(monkeypatch):
    gen = CandidateGenerator(llm=LLMClient(api_key="x"))
    parents = [
        _parent("amp", "ma(amplitude,20)", "amplitude", ic=0.016, resid=0.008),
        _parent("liq", "ma(turnover_rate,20)", "liquidity", ic=0.014, resid=0.007),
    ]
    got = gen._search_parents(parents, exclude=set(), limit=12)
    mechs = {str(p.get("mechanism")) for p in got}
    assert "amplitude" in mechs
    assert "liquidity" in mechs
