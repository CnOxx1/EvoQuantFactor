from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="seed_overnight_ma_20",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="overnight",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """ma(overnight,20)"""},
            tags=["dsl", "seed"],
            hypothesis="""隔夜收益的中期均值；A股跳空溢价先验""",
            entry_gate="research",
            expression="""ma(overnight,20)""",
            mechanism="overnight",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
