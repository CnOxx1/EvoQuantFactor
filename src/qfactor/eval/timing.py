from __future__ import annotations

import pandas as pd


def apply_trade_lag(panel: pd.DataFrame, lag: int) -> pd.DataFrame:
    """Shift a close-T signal so it is only tradable after `lag` sessions (T+lag)."""
    if lag < 0:
        raise ValueError("trade_lag cannot be negative (lookahead)")
    if lag == 0:
        return panel
    return panel.shift(lag)


def forward_close_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Return from close[T] to close[T+horizon]. Align with a lag-shifted signal."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    return close.shift(-horizon) / close - 1.0


def slice_eval_index(index: pd.Index, train_end: str | None, split: str) -> pd.Index:
    """Restrict dates to train (<= train_end) or holdout (> train_end)."""
    if not train_end or split == "full":
        return index
    keys = index.astype(str)
    if split == "train":
        return index[keys <= str(train_end)]
    if split == "holdout":
        return index[keys > str(train_end)]
    raise ValueError(f"Unknown split: {split}")


def drop_tail(panel: pd.DataFrame, n: int) -> pd.DataFrame:
    """Drop the last n rows (e.g. so H-day forward returns stay inside a split)."""
    if n <= 0 or panel.empty:
        return panel
    if len(panel.index) <= n:
        return panel.iloc[0:0]
    return panel.iloc[:-n]
