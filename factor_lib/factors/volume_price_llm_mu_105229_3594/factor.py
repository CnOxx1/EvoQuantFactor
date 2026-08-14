from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volume_price_llm_mu_105229_3594",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volume_price",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """mul(roc(close_adj,5),roc(vol,5))"""},
            tags=["dsl", "loop"],
            hypothesis="""当价格短期动量与成交量短期变化方向一致（同向上升或同向下降）时，未来5日收益倾向于延续该趋势；反之，背离时未来收益可能反转。；unlike 与library_cards中的表达式不同，本想法关注价格与成交量的同步性，而非振幅或影线特征，且不直接使用标准差或延迟算子。""",
            entry_gate="research",
            expression="""mul(roc(close_adj,5),roc(vol,5))""",
            mechanism="volume_price",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
