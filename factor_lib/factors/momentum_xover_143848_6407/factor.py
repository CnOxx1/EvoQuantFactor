from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="momentum_xover_143848_6407",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="momentum",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """sub(div(std(high, 10), std(low, 10)), rank(roc(overnight, 60)))"""},
            tags=["dsl", "loop"],
            hypothesis="""crossover volatility x amplitude""",
            entry_gate="research",
            expression="""sub(div(std(high, 10), std(low, 10)), rank(roc(overnight, 60)))""",
            mechanism="momentum",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
