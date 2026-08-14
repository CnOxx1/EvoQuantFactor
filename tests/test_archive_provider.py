import pandas as pd

from qfactor.data.archive_adapter import ArchiveAdapter
from qfactor.data.dataset import overlay_daily_basic


def test_archive_adapter_reads_pit_members_and_daily_basic(tmp_path):
    members = pd.DataFrame(
        {
            "trade_date": ["20201214", "20201214", "20210614"],
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "weight": [0.01, 0.02, 0.03],
        }
    )
    basic = pd.DataFrame(
        {
            "trade_date": ["20210104", "20210104"],
            "ts_code": ["000001.SZ", "000002.SZ"],
            "circ_mv": [100.0, 200.0],
            "turnover_rate": [1.0, 2.0],
        }
    )
    members_path = tmp_path / "members.parquet"
    basic_path = tmp_path / "basic.parquet"
    members.to_parquet(members_path, index=False)
    basic.to_parquet(basic_path, index=False)

    adapter = ArchiveAdapter(members_path, basic_path)
    hist = adapter.fetch_index_members_history("20201201", "20210501")
    out = adapter.fetch_daily_basic("000001.SZ", "20210101", "20210131")

    assert hist["trade_date"].tolist() == ["20201214", "20201214"]
    assert out["circ_mv"].tolist() == [100.0]
    assert out["turnover_rate"].tolist() == [1.0]


def test_overlay_daily_basic_records_archive_provenance():
    panel = pd.DataFrame(
        {
            "trade_date": ["20210104", "20210104"],
            "ts_code": ["000001.SZ", "000002.SZ"],
            "circ_mv": [10.0, 20.0],
            "turnover_rate": [0.1, 0.2],
        }
    )
    basic = pd.DataFrame(
        {
            "trade_date": ["20210104", "20210104"],
            "ts_code": ["000001.SZ", "000002.SZ"],
            "circ_mv": [100.0, 200.0],
            "turnover_rate": [1.0, 2.0],
        }
    )
    out, info = overlay_daily_basic(panel, basic, provider="archive")
    assert info["circ_mv_source"] == "archive_daily_basic"
    assert info["daily_basic_coverage"] == 1.0
    assert out["circ_mv"].tolist() == [100.0, 200.0]
