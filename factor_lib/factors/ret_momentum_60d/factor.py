from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RetMomentum60d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="ret_momentum_60d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="momentum",
            required_fields=["close_adj"],
            lookback=60,
            horizon=5,
            params={"window": 60},
            tags=["seed"],
            hypothesis="中期动量",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        close = ctx.panel("close_adj")
        raw = close.pct_change(60)
        return zscore(winsorize(raw))


def build() -> Factor:
    return RetMomentum60d()
