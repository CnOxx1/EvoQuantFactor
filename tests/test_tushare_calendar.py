import pandas as pd

from qfactor.data.dataset import DataService
from qfactor.data.tushare_adapter import TushareAdapter


def test_tushare_calendar_uses_baostock_not_trade_cal(monkeypatch):
    called = {"trade_cal": 0, "baostock": 0}

    class FakePro:
        def trade_cal(self, **kwargs):
            called["trade_cal"] += 1
            raise AssertionError("production must not call Tushare trade_cal")

    class FakeBS:
        def fetch_trade_calendar(self, start, end):
            called["baostock"] += 1
            assert start == "20240101"
            assert end == "20240110"
            return pd.DataFrame({"cal_date": ["20240102"], "is_open": [1]})

    monkeypatch.setattr(
        "qfactor.data.baostock_adapter.BaostockAdapter", FakeBS
    )
    ad = TushareAdapter.__new__(TushareAdapter)
    ad.pro = FakePro()
    ad.sleep_seconds = 0
    out = ad.fetch_trade_calendar("20240101", "20240110")
    assert called["trade_cal"] == 0
    assert called["baostock"] == 1
    assert list(out["cal_date"]) == ["20240102"]


def test_sync_calendar_ignores_tushare_adapter_calendar(monkeypatch):
    class FakeTSAdapter:
        name = "tushare"

        def fetch_trade_calendar(self, start, end):
            raise AssertionError("sync must not call Tushare trade_cal")

    class FakeBS:
        def fetch_trade_calendar(self, start, end):
            return pd.DataFrame({"cal_date": ["20240102"], "is_open": [1]})

    monkeypatch.setattr("qfactor.data.dataset.BaostockAdapter", FakeBS)
    cal, src = DataService.__new__(DataService)._fetch_trade_calendar(
        FakeTSAdapter(), "20240101", "20240110"
    )
    assert src == "baostock"
    assert list(cal["cal_date"]) == ["20240102"]
