from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="momentum_llm_mu_123653_4045",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="momentum",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(sub(roc(close_adj,20),roc(close_adj,60)),abs(roc(close_adj,60)))"""},
            tags=["dsl", "loop"],
            hypothesis="""T日收盘后，计算过去20日收盘价变化率（roc(close_adj,20)）与过去60日收盘价变化率（roc(close_adj,60)）之差，再除以过去60日收盘价变化率的绝对值（abs(roc(close_adj,60))），该因子衡量动量加速度的相对强度。截面排序后，值越大，未来5日收益越高。；unlike 不同于library_cards中的振幅类因子，本因子基于价格动量，且使用相对强度，而非直接使用roc或std。""",
            entry_gate="research",
            expression="""div(sub(roc(close_adj,20),roc(close_adj,60)),abs(roc(close_adj,60)))""",
            mechanism="momentum",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
