from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="reversal_60_3083",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="reversal",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """neg(roc(close_adj,60))"""},
            tags=["dsl", "loop"],
            hypothesis="""短期反转；模板启发表达式 neg(roc(close_adj,60))""",
            entry_gate="research",
            expression="""neg(roc(close_adj,60))""",
            mechanism="reversal",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
