from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="reversal_c20_105736_2911",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="reversal",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """ma(roc(ret_1d,20),10)"""},
            tags=["dsl", "loop"],
            hypothesis="""短期反转；compose ma(roc(ret_1d,20),10)""",
            entry_gate="research",
            expression="""ma(roc(ret_1d,20),10)""",
            mechanism="reversal",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
