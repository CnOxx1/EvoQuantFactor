from __future__ import annotations

import pandas as pd

from qfactor.data.dataset import DataService


class FactorContext:
    """Panel accessor shared by human and LLM-generated factors."""

    def __init__(self, bars: pd.DataFrame, universe_mask: pd.DataFrame | None = None):
        self._bars = bars.copy()
        self._bars["trade_date"] = self._bars["trade_date"].astype(str)
        self._cache: dict[str, pd.DataFrame] = {}
        self.universe_mask = universe_mask

    @classmethod
    def from_service(cls, service: DataService | None = None) -> "FactorContext":
        svc = service or DataService()
        bars = svc.load_bars()
        try:
            mask = svc.load_universe_mask()
        except Exception:
            mask = None
        return cls(bars, mask)

    def panel(self, field: str) -> pd.DataFrame:
        if field in self._cache:
            return self._cache[field].copy()
        if field not in self._bars.columns:
            raise KeyError(
                f"Field '{field}' not in dataset. Check data_catalog/fields.yaml"
            )
        pv = self._bars.pivot(index="trade_date", columns="ts_code", values=field)
        pv = pv.sort_index()
        if self.universe_mask is not None:
            # align and mask non-members to NaN
            mask = self.universe_mask.reindex(index=pv.index, columns=pv.columns)
            pv = pv.where(mask.fillna(False))
        self._cache[field] = pv
        return pv.copy()

    def shift_safe(self, df: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
        """Shift forward in time axis to enforce trade_lag / avoid lookahead misuse."""
        if periods < 0:
            raise ValueError("Negative shift is not allowed (lookahead risk)")
        return df.shift(periods)

    @property
    def tradable_mask(self) -> pd.DataFrame:
        close = self.panel("close")
        return close.notna()