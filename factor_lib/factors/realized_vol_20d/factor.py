from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RealizedVol20d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="realized_vol_20d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["ret_1d"],
            lookback=20,
            horizon=5,
            params={"window": 20},
            tags=["seed"],
            hypothesis="已实现波动",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        ret = ctx.panel("ret_1d")
        raw = ret.rolling(20).std()
        return zscore(winsorize(raw))


def build() -> Factor:
    return RealizedVol20d()
