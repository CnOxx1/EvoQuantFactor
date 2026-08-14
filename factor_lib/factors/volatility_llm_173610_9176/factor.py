from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volatility_llm_173610_9176",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(abs(ret_1d),add(ma(abs(ret_1d),20),std(close_adj,20)))"""},
            tags=["dsl", "loop"],
            hypothesis="""Higher ratio of absolute daily return to its 20-day moving average indicates recent volatility clustering, predicting lower future 5-day returns due to risk premium and mean reversion.；unlike div(abs(ret_1d),ma(turnover_rate,60))""",
            entry_gate="research",
            expression="""div(abs(ret_1d),add(ma(abs(ret_1d),20),std(close_adj,20)))""",
            mechanism="volatility",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
