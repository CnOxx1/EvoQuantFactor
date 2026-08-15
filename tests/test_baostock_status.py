from qfactor.data.baostock_adapter import BaostockAdapter


class _Result:
    error_code = "0"
    fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "turn",
        "tradestatus",
        "isST",
        "peTTM",
        "pbMRQ",
    ]

    def __init__(self):
        self.rows = iter(
            [
                [
                    "2024-01-02",
                    "sz.000001",
                    "10",
                    "11",
                    "9",
                    "10",
                    "10",
                    "1000",
                    "10000",
                    "1",
                    "0",
                    "1",
                    "5",
                    "1",
                ]
            ]
        )
        self.current = None

    def next(self):
        self.current = next(self.rows, None)
        return self.current is not None

    def get_row_data(self):
        return self.current


class _Api:
    def query_history_k_data_plus(self, *_args, **_kwargs):
        return _Result()


def test_baostock_bars_preserve_point_in_time_st_and_suspend():
    adapter = BaostockAdapter()
    adapter.bind_session(_Api())
    out = adapter.fetch_daily_bars("000001.SZ", "20240101", "20240131")
    assert bool(out.loc[0, "is_st"]) is True
    assert bool(out.loc[0, "is_suspended"]) is True
