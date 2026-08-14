import pytest

from qfactor.agent.generator import CandidateGenerator, _production_llm_cfg
from qfactor.agent.graph import build_production_graph
from qfactor.agent.llm import LLMClient
from qfactor.agent.loop import FactorLoop
from qfactor.settings import get_project_config


def test_production_llm_defaults():
    cfg = _production_llm_cfg(get_project_config())
    assert 0.3 <= cfg["llm_ratio"] <= 0.9
    assert "llm_mutate_share" not in cfg
    assert cfg["llm_review_ratio"] == 0.0
    assert cfg["llm_library_mutate_slots"] == 1
    assert cfg["llm_batch_size"] >= 1
    assert cfg.get("llm_retries", 1) >= 1


def test_require_llm_key_on_generate():
    gen = CandidateGenerator(llm=LLMClient(api_key=""))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        gen.generate_batch(n=2, theme="reversal")


def test_require_llm_key_on_loop():
    loop = FactorLoop(llm=LLMClient(api_key=""))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        loop.run(rounds=1, batch_size=1, resume=False)


def test_llm_review_cannot_veto():
    from qfactor.agent.reviewer import CandidateReviewer

    class _Fake:
        enabled = True

        def chat_json(self, *_a, **_k):
            return {"accept": False, "reason": "semantic no"}

    rev = CandidateReviewer(llm=_Fake())  # type: ignore[arg-type]
    out = rev.review(
        {"expression": "ma(ret_1d,5)", "hypothesis": "x"},
        set(),
        llm_spotcheck=True,
    )
    assert out["ok"] is True
    assert out["llm_note"]["accept"] is False


def test_langgraph_compiles():
    graph = build_production_graph()
    assert graph is not None
    spec = graph.get_graph()
    names = {n for n in spec.nodes}
    assert "decide" in names
    assert "generate" in names
    assert "review_validate" in names
    assert "persist" in names
