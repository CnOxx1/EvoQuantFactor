from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volatility_llm_mu_104538_4930",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(std(amplitude,10),ma(abs(ret_1d),10))"""},
            tags=["dsl", "loop"],
            hypothesis="""T日收盘后，用过去10日的振幅标准差除以过去10日收益绝对值均值，衡量波动率的不稳定性。该值越高，未来5日收益越低，呈负相关。；unlike 不同于library_cards中的div(std(high,10),std(low,10))，该因子使用振幅和收益，而非高低价标准差之比。""",
            entry_gate="research",
            expression="""div(std(amplitude,10),ma(abs(ret_1d),10))""",
            mechanism="volatility",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
