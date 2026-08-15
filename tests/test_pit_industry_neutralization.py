from types import SimpleNamespace

import pandas as pd

from qfactor.eval.service import EvalService


def _raw_panel():
    dates = pd.Index(["20240102", "20240103"])
    codes = pd.Index(["A", "B", "C", "D"])
    raw = pd.DataFrame(
        [[1.0, 3.0, 10.0, 14.0], [2.0, 4.0, 12.0, 16.0]],
        index=dates,
        columns=codes,
    )
    return dates, codes, raw


def test_prepare_eval_panel_accepts_point_in_time_industry_matrix():
    dates, codes, raw = _raw_panel()
    groups = pd.DataFrame(
        [["bank", "bank", "tech", "tech"], ["bank", "bank", "tech", "tech"]],
        index=dates,
        columns=codes,
    )
    service = object.__new__(EvalService)
    service.cfg = SimpleNamespace(eval={"eval": {"neutralize_industry": True, "neutralize_size": False}})
    service._industry_groups = lambda: groups

    prepared, used = service._prepare_eval_panel(raw)

    assert used == ["industry"]
    assert prepared.loc["20240102", ["A", "B"]].mean() == 0.0
    assert prepared.loc["20240102", ["C", "D"]].mean() == 0.0


def test_prepare_eval_panel_skips_static_industry_and_estimated_size():
    dates, codes, raw = _raw_panel()
    circ = pd.DataFrame(
        [[100.0, 200.0, 300.0, 400.0], [110.0, 210.0, 310.0, 410.0]],
        index=dates,
        columns=codes,
    )
    service = object.__new__(EvalService)
    service.cfg = SimpleNamespace(
        eval={
            "eval": {
                "neutralize_industry": True,
                "neutralize_size": True,
                "neutralize_require_vendor_circ_mv": True,
            }
        }
    )
    service._industry_map = None
    service._ctx = SimpleNamespace(
        panel=lambda field: circ if field == "circ_mv" else (_ for _ in ()).throw(KeyError(field))
    )
    service.data = SimpleNamespace(
        status=lambda: {"meta": {"circ_mv_source": "estimated"}},
        load_industry=lambda: pd.DataFrame(
            {"ts_code": list(codes), "industry": ["bank", "bank", "tech", "tech"]}
        ),
    )

    prepared, used = service._prepare_eval_panel(raw)

    assert used == []
    pd.testing.assert_frame_equal(prepared, raw)


def test_prepare_eval_panel_uses_vendor_circ_mv_and_pit_industry():
    dates, codes, raw = _raw_panel()
    groups = pd.DataFrame(
        [["bank", "bank", "tech", "tech"], ["bank", "bank", "tech", "tech"]],
        index=dates,
        columns=codes,
    )
    circ = pd.DataFrame(
        [[100.0, 200.0, 300.0, 400.0], [110.0, 210.0, 310.0, 410.0]],
        index=dates,
        columns=codes,
    )
    service = object.__new__(EvalService)
    service.cfg = SimpleNamespace(
        eval={
            "eval": {
                "neutralize_industry": True,
                "neutralize_size": True,
                "neutralize_require_vendor_circ_mv": True,
            }
        }
    )
    service._industry_groups = lambda: groups
    service._ctx = SimpleNamespace(panel=lambda field: circ)
    service.data = SimpleNamespace(status=lambda: {"meta": {"circ_mv_source": "tushare_daily_basic"}})

    prepared, used = service._prepare_eval_panel(raw)

    assert used == ["industry", "circ_mv"]
    assert prepared.notna().all().all()


def test_vendor_circ_mv_ok_requires_daily_basic_source():
    service = object.__new__(EvalService)
    service.cfg = SimpleNamespace(eval={"eval": {"neutralize_require_vendor_circ_mv": True}})
    service.data = SimpleNamespace(status=lambda: {"meta": {"circ_mv_source": "estimated"}})
    assert service._vendor_circ_mv_ok() is False

    service.data = SimpleNamespace(status=lambda: {"meta": {"circ_mv_source": "tushare_daily_basic"}})
    assert service._vendor_circ_mv_ok() is True

    service.data = SimpleNamespace(status=lambda: {"meta": {"circ_mv_source": "archive_daily_basic"}})
    assert service._vendor_circ_mv_ok() is True

    service.data = SimpleNamespace(
        status=lambda: {"meta": {"limitations": ["circ_mv estimated from amount/turnover"]}}
    )
    assert service._vendor_circ_mv_ok() is False

    service.cfg = SimpleNamespace(eval={"eval": {"neutralize_require_vendor_circ_mv": False}})
    service.data = SimpleNamespace(status=lambda: {"meta": {"circ_mv_source": "estimated"}})
    assert service._vendor_circ_mv_ok() is True
