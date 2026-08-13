from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RetReversal20d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="ret_reversal_20d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="reversal",
            required_fields=["close_adj"],
            lookback=20,
            horizon=5,
            params={"window": 20},
            tags=["seed"],
            hypothesis="短期收益反转",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        close = ctx.panel("close_adj")
        raw = -close.pct_change(20)
        return zscore(winsorize(raw))


def build() -> Factor:
    return RetReversal20d()
