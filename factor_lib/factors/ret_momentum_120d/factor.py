from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RetMomentum120d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="ret_momentum_120d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="momentum",
            required_fields=["close_adj"],
            lookback=120,
            horizon=5,
            params={"window": 120},
            tags=["seed"],
            hypothesis="中期动量",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        close = ctx.panel("close_adj")
        raw = close.pct_change(120)
        return zscore(winsorize(raw))


def build() -> Factor:
    return RetMomentum120d()
