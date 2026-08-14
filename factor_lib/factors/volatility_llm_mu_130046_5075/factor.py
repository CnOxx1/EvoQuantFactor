from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volatility_llm_mu_130046_5075",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """neg(div(delta(std(close,20),5),std(close,20)))"""},
            tags=["dsl", "loop"],
            hypothesis="""股票的未来5日收益与近期日内波动率的变化率负相关，即波动率上升的股票未来表现较差。；unlike 不同于library_cards中的div(std(high,10),std(low,10))，该因子关注波动率的时间变化，而非高低价波动率之比。""",
            entry_gate="research",
            expression="""neg(div(delta(std(close,20),5),std(close,20)))""",
            mechanism="volatility",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
