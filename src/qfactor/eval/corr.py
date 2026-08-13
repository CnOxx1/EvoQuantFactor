from __future__ import annotations

import pandas as pd

from qfactor.eval.ic import cs_spearman_series


def max_corr_with_library(
    factor: pd.DataFrame,
    others: dict[str, pd.DataFrame],
    min_obs: int = 5,
) -> dict:
    best_name = None
    best_corr = 0.0
    for name, other in others.items():
        s = cs_spearman_series(factor, other, min_obs=min_obs)
        if s.empty:
            continue
        c = abs(float(s.mean()))
        if c > best_corr:
            best_corr = c
            best_name = name
    return {"max_corr": best_corr, "max_corr_with": best_name}
