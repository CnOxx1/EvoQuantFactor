from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class Amplitude20d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="amplitude_20d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="volatility",
            required_fields=["high", "low", "close"],
            lookback=20,
            horizon=5,
            params={"window": 20},
            tags=["seed"],
            hypothesis="振幅均值",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        high = ctx.panel("high")
        low = ctx.panel("low")
        close = ctx.panel("close").replace(0, pd.NA)
        amp = (high - low) / close
        raw = amp.rolling(20).mean()
        return zscore(winsorize(raw))


def build() -> Factor:
    return Amplitude20d()
