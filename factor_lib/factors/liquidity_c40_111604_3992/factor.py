from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="liquidity_c40_111604_3992",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="liquidity",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(std(turnover_rate,40),ma(abs(turnover_rate),40))"""},
            tags=["dsl", "loop"],
            hypothesis="""流动性/成交；compose div(std(turnover_rate,40),ma(abs(turnover_rate),40))""",
            entry_gate="research",
            expression="""div(std(turnover_rate,40),ma(abs(turnover_rate),40))""",
            mechanism="liquidity",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
