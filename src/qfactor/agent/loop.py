from __future__ import annotations

from typing import Any

from qfactor.agent.graph import run_production_graph
from qfactor.agent.llm import LLMClient
from qfactor.eval.service import EvalService
from qfactor.factor.registry import FactorRegistry
from qfactor.settings import ProjectConfig, get_project_config

# Re-export DSL helper for callers that imported it from loop
from qfactor.agent.graph import _dsl_factor_code as _dsl_factor_code  # noqa: F401


class FactorMiningAgent:
    """Legacy single-theme miner kept for compatibility."""

    def __init__(self, cfg: ProjectConfig | None = None, llm: LLMClient | None = None):
        self.cfg = cfg or get_project_config()
        self.llm = llm or LLMClient()
        self.registry = FactorRegistry(self.cfg)
        self.eval = EvalService(self.cfg)

    def mine(
        self,
        theme: str,
        max_iters: int = 3,
        gate_name: str = "research",
        llm_ratio: float | None = None,
        llm_review_ratio: float | None = None,
    ) -> dict[str, Any]:
        loop = FactorLoop(self.cfg, self.llm)
        return loop.run(
            rounds=max_iters,
            batch_size=max(4, max_iters),
            theme=theme,
            gate_name=gate_name,
            resume=False,
            llm_ratio=llm_ratio,
            llm_review_ratio=llm_review_ratio,
        )


class FactorLoop:
    """
    LangGraph-orchestrated production loop:
    decide -> generate -> review_validate -> persist (-> repeat),
    then production re-eval of newly screened factors.
    Requires OPENAI_API_KEY; no offline fallback.
    """

    def __init__(self, cfg: ProjectConfig | None = None, llm: LLMClient | None = None):
        self.cfg = cfg or get_project_config()
        self.llm = llm or LLMClient()

    def run(
        self,
        rounds: int = 5,
        batch_size: int = 8,
        theme: str | None = None,
        gate_name: str = "research",
        resume: bool = True,
        llm_ratio: float | None = None,
        llm_review_ratio: float | None = None,
        llm_spotcheck_every: int | None = None,
    ) -> dict[str, Any]:
        return run_production_graph(
            cfg=self.cfg,
            llm=self.llm,
            rounds=rounds,
            batch_size=batch_size,
            theme=theme,
            gate_name=gate_name,
            resume=resume,
            llm_ratio=llm_ratio,
            llm_review_ratio=llm_review_ratio,
            llm_spotcheck_every=llm_spotcheck_every,
        )
