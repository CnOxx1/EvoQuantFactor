from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="momentum_llm_mu_121400_2203",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="amplitude",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """div(ma(amplitude,20),ma(amplitude,60))"""},
            tags=["dsl", "loop"],
            hypothesis="""T日收盘后，用过去20日的平均真实波幅（以振幅近似）除以过去60日的平均振幅，得到的比值越高，未来5日收益越低。；unlike library_cards中的div(std(amplitude,40),std(overnight,40))是波动率比值，本因子是均值比值，且窗口不同，不是同一件事。""",
            entry_gate="research",
            expression="""div(ma(amplitude,20),ma(amplitude,60))""",
            mechanism="amplitude",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
