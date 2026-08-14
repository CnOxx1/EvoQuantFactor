from __future__ import annotations

import pandas as pd

from qfactor.dsl.eval_expr import evaluate_expression
from qfactor.dsl.parser import parse_expression
from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class DSLFactor(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="shadow_llm_mu_144653_2796",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="shadow",
            required_fields=["close", "open", "high", "low"],
            lookback=20,
            horizon=5,
            params={"expression": """neg(ma(div(upper_shadow,amplitude),20))"""},
            tags=["dsl", "loop"],
            hypothesis="""T日收盘后，用上影线相对振幅的比值（upper_shadow/amplitude）的20日移动平均，预测未来5日截面收益：该比值越高，未来5日收益越低。；unlike 不同于library_cards中的div(std(overnight,40),std(amplitude,40))，该因子使用upper_shadow与amplitude的比值，且用移动平均平滑，而非标准差之比。""",
            entry_gate="research",
            expression="""neg(ma(div(upper_shadow,amplitude),20))""",
            mechanism="shadow",
        )

    def compute(self, ctx) -> pd.DataFrame:
        expr = parse_expression(self.spec.expression or self.spec.params["expression"])
        raw = evaluate_expression(expr, ctx)
        return zscore(winsorize(raw))


def build() -> Factor:
    return DSLFactor()
