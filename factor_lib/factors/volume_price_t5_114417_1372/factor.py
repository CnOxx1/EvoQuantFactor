from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volume_price_t5_114417_1372",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volume_price",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """mul(rank(roc(close_adj,5)),rank(roc(vol,5)))"""},
            tags=["dsl", "loop"],
            hypothesis="""量价配合；模板启发表达式 mul(rank(roc(close_adj,5)),rank(roc(vol,5)))""",
            entry_gate="research",
            expression="""mul(rank(roc(close_adj,5)),rank(roc(vol,5)))""",
            mechanism="volume_price",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
