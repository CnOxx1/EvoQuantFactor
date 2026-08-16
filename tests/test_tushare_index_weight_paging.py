import pandas as pd

from qfactor.data.tushare_adapter import TushareAdapter, fetch_index_weight_pages
from qfactor.data.tushare_adapter import _auth_lock_wait
from qfactor.data.vendor_archive_fetch import (
    _calendar_covers_window,
    _normalize_sw_roster,
    codes_missing_window_industry,
    expand_industry_to_calendar,
    fetch_csi100_members,
)


class _PagedPro:
    def __init__(self, pages: dict[int, pd.DataFrame]):
        self.pages = pages
        self.calls: list[dict] = []

    def index_weight(self, **kwargs):
        self.calls.append(kwargs)
        offset = int(kwargs.get("offset") or 0)
        return self.pages.get(offset, pd.DataFrame())


def test_index_weight_pages_concatenates_one_row_first_page():
    first = pd.DataFrame(
        {
            "index_code": ["000903.SH"],
            "con_code": ["600519.SH"],
            "trade_date": ["20240131"],
            "weight": [9.814],
        }
    )
    rest = pd.DataFrame(
        {
            "index_code": ["000903.SH"] * 2,
            "con_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20240131", "20240131"],
            "weight": [1.0, 2.0],
        }
    )
    pro = _PagedPro({0: first, 1: rest})
    out = fetch_index_weight_pages(
        pro, index_code="000903.SH", start_date="20240101", end_date="20240131"
    )
    assert set(out["con_code"]) == {"600519.SH", "000001.SZ", "000002.SZ"}
    assert len(pro.calls) >= 2
    assert pro.calls[1]["offset"] == 1


def test_index_weight_pages_stops_when_offset_repeats():
    page = pd.DataFrame(
        {
            "index_code": ["000903.SH"],
            "con_code": ["600519.SH"],
            "trade_date": ["20241231"],
            "weight": [1.0],
        }
    )
    pro = _PagedPro({0: page, 1: page})
    out = fetch_index_weight_pages(pro, index_code="000903.SH", trade_date="20241231")
    assert len(out) == 1


def test_range_fetch_rejects_one_name_per_month(monkeypatch):
    class FakePro:
        def index_weight(self, **kwargs):
            start = kwargs.get("start_date", "20240101")
            month = start[:6]
            return pd.DataFrame(
                {
                    "index_code": ["000903.SH"],
                    "con_code": ["600519.SH"],
                    "trade_date": [month + "28"],
                    "weight": [9.8],
                }
            )

        def index_member(self, **kwargs):
            return pd.DataFrame()

        def index_member_all(self, **kwargs):
            return pd.DataFrame()

    ad = TushareAdapter.__new__(TushareAdapter)
    ad.pro = FakePro()
    ad.index_code = "000903.SH"
    ad.sleep_seconds = 0
    ad._sleep = lambda: None
    out = ad._fetch_index_weight_range("20240101", "20240331")
    # One name per month is kept as raw months, but must not early-return as complete.
    # After monthly loop we still only have 茅台 — caller/stats will reject PIT.
    assert out["ts_code"].nunique() == 1


def test_expand_industry_out_date_is_exclusive():
    roster = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "industry": ["银行", "非银金融"],
            "in_date": ["20200101", "20240601"],
            "out_date": ["20240601", "99991231"],
        }
    )
    dates = ["20240531", "20240601", "20240603"]
    out = expand_industry_to_calendar(roster, dates, ["000001.SZ"])
    by_date = dict(zip(out["trade_date"], out["industry"]))
    assert by_date["20240531"] == "银行"
    assert by_date["20240601"] == "非银金融"
    assert by_date["20240603"] == "非银金融"


def test_member_fetch_skips_months_already_on_disk():
    class _Pro:
        def __init__(self):
            self.months: list[str] = []

        def index_weight(self, **kwargs):
            start = kwargs["start_date"]
            self.months.append(start[:6])
            return pd.DataFrame(
                {
                    "index_code": ["000903.SH"] * 2,
                    "con_code": ["000001.SZ", "600519.SH"],
                    "trade_date": [start[:6] + "28", start[:6] + "28"],
                    "weight": [1.0, 2.0],
                }
            )

    pro = _Pro()
    out = fetch_csi100_members(
        pro, "20150901", "20151130", sleep_seconds=0, skip_months={"201510"}
    )
    assert "201510" not in pro.months
    assert set(out["trade_date"].astype(str).str[:6]) == {"201509", "201511"}


def test_auth_lock_wait_parses_retry_seconds():
    wait = _auth_lock_wait(
        RuntimeError("授权验证失败：授权码正在被其他设备使用，请等待 58 秒后重试")
    )
    assert wait == 63.0
    assert _auth_lock_wait(RuntimeError("读取 服务端 响应超时（已等待 60 秒内）")) == 8.0
    assert _auth_lock_wait(RuntimeError("no permission")) is None


def test_normalize_sw_roster_uses_l1_name_and_open_out_date():
    raw = pd.DataFrame(
        {
            "ts_code": ["000333.SZ"],
            "l1_name": ["家用电器"],
            "in_date": ["20130118"],
            "out_date": [None],
        }
    )
    out = _normalize_sw_roster(raw)
    assert out.iloc[0]["industry"] == "家用电器"
    assert out.iloc[0]["out_date"] == "99991231"


def test_codes_missing_window_industry_ignores_expired_interval():
    roster = pd.DataFrame(
        {
            "ts_code": ["002352.SZ", "000001.SZ"],
            "industry": ["机械设备", "银行"],
            "in_date": ["20100118", "19910403"],
            "out_date": ["20170228", "99991231"],
        }
    )
    missing = codes_missing_window_industry(
        roster, ["002352.SZ", "000001.SZ", "000333.SZ"], "20191201", "20251231"
    )
    assert missing == ["002352.SZ", "000333.SZ"]


def test_calendar_covers_window_rejects_short_research_slice():
    short = ["20240102", "20251231"]
    assert not _calendar_covers_window(short, "20191201", "20251231")
    assert _calendar_covers_window(short, "20240101", "20251231")
    # Sunday window start still matches a Monday first open day.
    assert _calendar_covers_window(["20191202", "20251231"], "20191201", "20251231")
    assert not _calendar_covers_window([], "20191201", "20251231")
