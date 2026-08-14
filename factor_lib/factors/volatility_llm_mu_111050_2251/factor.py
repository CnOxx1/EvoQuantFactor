from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volatility_llm_mu_111050_2251",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """neg(std(overnight,20))"""},
            tags=["dsl", "loop"],
            hypothesis="""隔夜收益波动率高的股票未来5日收益倾向于更低，隔夜收益的标准差与未来收益负相关。；unlike 不同于library_cards中的div(std(overnight,40),std(amplitude,40))，该因子仅使用隔夜收益的标准差，不涉及振幅。""",
            entry_gate="research",
            expression="""neg(std(overnight,20))""",
            mechanism="volatility",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
