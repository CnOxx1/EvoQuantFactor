from __future__ import annotations

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.registry import FactorRegistry
from qfactor.factor.transforms import winsorize, zscore


def _code_reversal(window: int) -> str:
    return f'''from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RetReversal{window}d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="ret_reversal_{window}d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="reversal",
            required_fields=["close_adj"],
            lookback={window},
            horizon=5,
            params={{"window": {window}}},
            tags=["seed"],
            hypothesis="短期收益反转",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        close = ctx.panel("close_adj")
        raw = -close.pct_change({window})
        return zscore(winsorize(raw))


def build() -> Factor:
    return RetReversal{window}d()
'''


def _code_momentum(window: int) -> str:
    return f'''from __future__ import annotations

import pandas as pd

from qfactor.factor.base import Factor, FactorSpec
from qfactor.factor.transforms import winsorize, zscore


class RetMomentum{window}d(Factor):
    def __init__(self):
        self.spec = FactorSpec(
            name="ret_momentum_{window}d",
            version="0.1.0",
            status="draft",
            family="price_volume",
            category="momentum",
            required_fields=["close_adj"],
            lookback={window},
            horizon=5,
            params={{"window": {window}}},
            tags=["seed"],
            hypothesis="中期动量",
            entry_gate="research",
        )

    def compute(self, ctx) -> pd.DataFrame:
        close = ctx.panel("close_adj")
        raw = close.pct_change({window})
        return zscore(winsorize(raw))


def build() -> Factor:
    return RetMomentum{window}d()
'''


VOL_CODE = '''from __future__ import annotations

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
'''

AMIHUD_CODE = '''from __future__ import annotations

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
'''

VOLUME_SHOCK_CODE = '''from __future__ import annotations

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
'''

AMPLITUDE_CODE = '''from __future__ import annotations

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
'''


def install_seed_factors() -> list[str]:
    reg = FactorRegistry()
    items: list[tuple[FactorSpec, str]] = []

    for w in (5, 10, 20):
        items.append(
            (
                FactorSpec(
                    name=f"ret_reversal_{w}d",
                    family="price_volume",
                    category="reversal",
                    required_fields=["close_adj"],
                    lookback=w,
                    tags=["seed"],
                    hypothesis="短期收益反转",
                    entry_gate="research",
                ),
                _code_reversal(w),
            )
        )
    for w in (60, 120):
        items.append(
            (
                FactorSpec(
                    name=f"ret_momentum_{w}d",
                    family="price_volume",
                    category="momentum",
                    required_fields=["close_adj"],
                    lookback=w,
                    tags=["seed"],
                    hypothesis="中期动量",
                    entry_gate="research",
                ),
                _code_momentum(w),
            )
        )

    extras = [
        (
            FactorSpec(
                name="realized_vol_20d",
                family="price_volume",
                category="volatility",
                required_fields=["ret_1d"],
                lookback=20,
                tags=["seed"],
                entry_gate="research",
            ),
            VOL_CODE,
        ),
        (
            FactorSpec(
                name="amihud_20d",
                family="price_volume",
                category="liquidity",
                required_fields=["ret_1d", "amount"],
                lookback=20,
                tags=["seed"],
                entry_gate="research",
            ),
            AMIHUD_CODE,
        ),
        (
            FactorSpec(
                name="volume_shock_20d",
                family="price_volume",
                category="liquidity",
                required_fields=["vol"],
                lookback=20,
                tags=["seed"],
                entry_gate="research",
            ),
            VOLUME_SHOCK_CODE,
        ),
        (
            FactorSpec(
                name="amplitude_20d",
                family="price_volume",
                category="volatility",
                required_fields=["high", "low", "close"],
                lookback=20,
                tags=["seed"],
                entry_gate="research",
            ),
            AMPLITUDE_CODE,
        ),
    ]
    items.extend(extras)

    saved = []
    for spec, code in items:
        reg.save_factor_files(spec, code, source="seed")
        saved.append(spec.name)
    return saved