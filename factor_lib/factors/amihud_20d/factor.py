from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class Amihud20d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="amihud_20d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="liquidity",
            required_fields=["ret_1d", "amount"],
            lookback=20,
            horizon=5,
            params={"window": 20},
            tags=["seed"],
            hypothesis="Amihud非流动性",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        ret = ctx.panel("ret_1d").abs()
        amount = ctx.panel("amount").replace(0, pd.NA)
        raw = (ret / amount).rolling(20).mean()
        return zscore(winsorize(raw))


def build() -> Factor:
    return Amihud20d()
