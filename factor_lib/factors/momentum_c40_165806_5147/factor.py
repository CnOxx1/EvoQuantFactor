from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="momentum_c40_165806_5147",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="momentum",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(std(open,40),ma(abs(open),40))"""},
            tags=["dsl", "loop"],
            hypothesis="""中期动量；compose div(std(open,40),ma(abs(open),40))""",
            entry_gate="research",
            expression="""div(std(open,40),ma(abs(open),40))""",
            mechanism="momentum",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
