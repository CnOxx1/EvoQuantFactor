from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="shadow_llm_150832_8859",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="shadow",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """mul(rank(upper_shadow), rank(turnover_rate))"""},
            tags=["dsl", "loop"],
            hypothesis="""高换手率伴随长上影线预示未来5日收益走低，因为上影线反映抛压，换手率放大确认资金出逃。；unlike 不同于已有的 div(ma(upper_shadow,N),ma(turnover_rate,N)) 候选，该想法使用 rank 和乘法，强调截面排序后的交互效应。""",
            entry_gate="research",
            expression="""mul(rank(upper_shadow), rank(turnover_rate))""",
            mechanism="shadow",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
