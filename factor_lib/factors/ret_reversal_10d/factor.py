from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RetReversal10d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="ret_reversal_10d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="reversal",
            required_fields=["close_adj"],
            lookback=10,
            horizon=5,
            params={"window": 10},
            tags=["seed"],
            hypothesis="短期收益反转",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        close = ctx.panel("close_adj")
        raw = -close.pct_change(10)
        return zscore(winsorize(raw))


def build() -> Factor:
    return RetReversal10d()
