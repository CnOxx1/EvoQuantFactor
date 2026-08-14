from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="liquidity_llm_mu_103559_8800",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="liquidity",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """neg(ma(turnover_rate,5))"""},
            tags=["dsl", "loop"],
            hypothesis="""T日收盘后，过去5日平均换手率与未来5日收益负相关，即高换手率股票未来表现较差。；unlike 不同于library_cards中的振幅类因子，本因子聚焦于流动性而非价格波动。""",
            entry_gate="research",
            expression="""neg(ma(turnover_rate,5))""",
            mechanism="liquidity",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
