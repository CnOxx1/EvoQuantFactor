from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="momentum_llm_mu_104647_6042",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="amplitude",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """sub(ma(amplitude,10),ma(amplitude,40))"""},
            tags=["dsl", "loop"],
            hypothesis="""T日收盘后，计算过去10日平均振幅（amplitude的10日均值）与过去40日平均振幅之差，该差值在截面上的排序与未来5日收益负相关。；unlike 该因子是振幅均值差，与library_cards中的振幅变化率（如delay(roc(amplitude,40),60)）不同，且不是简单改变窗口。""",
            entry_gate="research",
            expression="""sub(ma(amplitude,10),ma(amplitude,40))""",
            mechanism="amplitude",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
