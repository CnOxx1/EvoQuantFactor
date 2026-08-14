from types import SimpleNamespace

import pandas as pd

from qfactor.eval.service import EvalService


def test_prepare_eval_panel_accepts_point_in_time_industry_matrix():
    dates = pd.Index(["20240102", "20240103"])
    codes = pd.Index(["A", "B", "C", "D"])
    groups = pd.DataFrame(
        [["bank", "bank", "tech", "tech"], ["bank", "bank", "tech", "tech"]],
        index=dates,
        columns=codes,
    )
    service = object.__new__(EvalService)
    service.cfg = SimpleNamespace(eval={"eval": {"neutralize_industry": True, "neutralize_size": False}})
    service._industry_groups = lambda: groups
    raw = pd.DataFrame([[1.0, 3.0, 10.0, 14.0], [2.0, 4.0, 12.0, 16.0]], index=dates, columns=codes)

    prepared, used = service._prepare_eval_panel(raw)

    assert used == ["industry"]
    assert prepared.loc["20240102", ["A", "B"]].mean() == 0.0
    assert prepared.loc["20240102", ["C", "D"]].mean() == 0.0
