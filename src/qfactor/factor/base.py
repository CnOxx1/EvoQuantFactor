from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from qfactor.factor.context import FactorContext


class FactorSpec(BaseModel):
    name: str
    version: str = "0.1.0"
    status: str = "draft"  # draft|screened|candidate|approved|deprecated
    family: str = "price_volume"
    category: str = "unknown"
    universe: str = "csi100"
    frequency: str = "daily"
    required_fields: list[str] = Field(default_factory=list)
    lookback: int = 20
    horizon: int = 5
    neutralize: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    hypothesis: str = ""
    entry_gate: str = "default"
    expression: str | None = None
    mechanism: str | None = None
    expr_hash: str | None = None
    signal_hold_days: int = 5  # compute() is daily; gates score the H-day hold signal


class Factor(ABC):
    spec: FactorSpec

    @abstractmethod
    def compute(self, ctx: FactorContext) -> pd.DataFrame:
        """Return panel: index=trade_date, columns=ts_code."""