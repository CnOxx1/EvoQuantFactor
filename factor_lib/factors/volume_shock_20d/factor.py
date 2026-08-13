from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class VolumeShock20d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="volume_shock_20d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="liquidity",
            required_fields=["vol"],
            lookback=20,
            horizon=5,
            params={"window": 20},
            tags=["seed"],
            hypothesis="成交量相对20日均值的冲击",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        vol = ctx.panel("vol")
        raw = vol / vol.rolling(20).mean() - 1.0
        return zscore(winsorize(raw))


def build() -> Factor:
    return VolumeShock20d()
