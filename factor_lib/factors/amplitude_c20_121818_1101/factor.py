from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="amplitude_c20_121818_1101",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="amplitude",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """std(roc(amplitude,20),10)"""},
            tags=["dsl", "loop"],
            hypothesis="""振幅结构；compose std(roc(amplitude,20),10)""",
            entry_gate="research",
            expression="""std(roc(amplitude,20),10)""",
            mechanism="amplitude",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
