from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="momentum_llm_143848_8537",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="liquidity",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(ma(turnover_rate,20),ma(abs(ret_1d),20))"""},
            tags=["dsl", "loop"],
            hypothesis="""The ratio of the 20-day moving average of turnover rate to the 20-day moving average of absolute return (ret_1d) predicts future 5-day returns: higher turnover relative to absolute return (i.e., high turnover with low price movement) is associated with higher future returns, indicating that active trading without price change may precede upward moves.；unlike This is not the same as the candidate 'div(abs(ret_1d),ma(turnover_rate,60))' which uses absolute return in the numerator and a longer moving average of turnover; here we use the inverse ratio with a shorter window and no absolute value on return.""",
            entry_gate="research",
            expression="""div(ma(turnover_rate,20),ma(abs(ret_1d),20))""",
            mechanism="liquidity",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
