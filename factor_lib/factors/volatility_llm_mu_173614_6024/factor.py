from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volatility_llm_mu_173614_6024",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """neg(div(std(ret_1d,20), std(ret_1d,5)))"""},
            tags=["dsl", "loop"],
            hypothesis="""股票收益率的波动率与未来5日收益负相关，即高波动率股票未来5日收益较低。；unlike 不同于已有的div(std(close_adj,N),std(close_adj,N))，该因子使用收益率的标准差而非价格的标准差。""",
            entry_gate="research",
            expression="""neg(div(std(ret_1d,20), std(ret_1d,5)))""",
            mechanism="volatility",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
