from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qfactor.data.akshare_adapter import AkshareAdapter
from qfactor.data.archive_adapter import ArchiveAdapter
from qfactor.data.archive_ingest import (
    ingest_archive_role,
    resolve_evidence_provider,
    validate_archive_frame,
    validate_registered_archives,
)
from qfactor.data.vendor_normalize import (
    normalize_panel,
    normalize_trade_date,
    normalize_ts_code,
    rename_vendor_columns,
)


def _cfg(root: Path, providers: dict | None = None) -> SimpleNamespace:
    archive = {
        "universe_history": "data/raw/providers/csi100_members.parquet",
        "daily_basic": "data/raw/providers/daily_basic.parquet",
        "security_status": "data/raw/providers/security_status.parquet",
        "corporate_actions": "data/raw/providers/corporate_actions.parquet",
        "risk_exposures": "data/raw/providers/risk_exposures.parquet",
        "industry_history": "data/raw/providers/industry_history.parquet",
    }
    return SimpleNamespace(
        root=root,
        data_sources={
            "providers": providers or {"universe": "auto", "daily_basic": "auto"},
            "archive": archive,
        },
    )


def test_normalize_vendor_codes_and_dates():
    assert normalize_ts_code("000001.XSHE") == "000001.SZ"
    assert normalize_ts_code("600000.XSHG") == "600000.SH"
    assert normalize_ts_code("SZ000001") == "000001.SZ"
    assert normalize_ts_code("sh600000") == "600000.SH"
    assert normalize_ts_code("000001") == "000001.SZ"
    assert normalize_ts_code("600000") == "600000.SH"
    assert normalize_trade_date("2024-01-02") == "20240102"
    assert normalize_trade_date("2024/01/02") == "20240102"
    assert normalize_trade_date(20240102) == "20240102"


def test_rename_wind_choice_columns_without_inventing_total_mv():
    raw = pd.DataFrame(
        {
            "TRADE_DT": ["2024-01-02"],
            "S_INFO_WINDCODE": ["000001.SZ"],
            "S_VAL_MV": [100.0],
            "total_mv": [999.0],
            "换手率": [1.5],
        }
    )
    out = rename_vendor_columns(raw)
    assert "trade_date" in out.columns
    assert "ts_code" in out.columns
    assert "circ_mv" in out.columns
    assert "turnover_rate" in out.columns
    assert out["circ_mv"].tolist() == [100.0]
    assert "total_mv" in out.columns


def test_archive_adapter_reads_wind_aliases(tmp_path):
    members = pd.DataFrame(
        {
            "日期": ["2024-06-17", "2024-06-17"],
            "成分券代码": ["000001.XSHE", "600000.XSHG"],
            "权重": [0.01, 0.02],
        }
    )
    basic = pd.DataFrame(
        {
            "TRADE_DT": ["20240102"],
            "S_INFO_WINDCODE": ["000001.SZ"],
            "S_VAL_MV": [123.0],
            "TURNOVER_RATE": [2.0],
        }
    )
    members_path = tmp_path / "members.parquet"
    basic_path = tmp_path / "basic.parquet"
    members.to_parquet(members_path, index=False)
    basic.to_parquet(basic_path, index=False)
    adapter = ArchiveAdapter(members_path, basic_path)
    hist = adapter.fetch_index_members_history("20240101", "20241231")
    out = adapter.fetch_daily_basic("000001.SZ", "20240101", "20240131")
    assert set(hist["ts_code"]) == {"000001.SZ", "600000.SH"}
    assert out["circ_mv"].tolist() == [123.0]


def test_ingest_archive_writes_contract_parquet(tmp_path):
    src = tmp_path / "wind_members.csv"
    pd.DataFrame(
        {
            "TRADE_DT": ["2024-01-02", "2024-01-02"],
            "S_INFO_WINDCODE": ["000001.XSHE", "600000.XSHG"],
            "权重": [1.0, 2.0],
        }
    ).to_csv(src, index=False)
    dest = tmp_path / "out" / "csi100_members.parquet"
    report = ingest_archive_role("universe", src, dest=dest)
    assert report["ok"] is True
    assert dest.exists()
    written = pd.read_parquet(dest)
    assert list(written.columns)[:2] == ["trade_date", "ts_code"]
    assert set(written["ts_code"]) == {"000001.SZ", "600000.SH"}
    assert written["trade_date"].tolist() == ["20240102", "20240102"]


def test_ingest_rejects_bars_as_daily_basic(tmp_path):
    src = tmp_path / "bars.csv"
    pd.DataFrame(
        {
            "trade_date": ["20240102"],
            "ts_code": ["000001.SZ"],
            "close": [10.0],
            "amount": [1000.0],
        }
    ).to_csv(src, index=False)
    with pytest.raises(ValueError, match="missing_columns:circ_mv"):
        ingest_archive_role("daily_basic", src, dest=tmp_path / "basic.parquet")


def test_ingest_rejects_duplicate_keys(tmp_path):
    src = tmp_path / "dup.csv"
    pd.DataFrame(
        {
            "trade_date": ["20240102", "20240102"],
            "ts_code": ["000001.SZ", "000001.SZ"],
            "circ_mv": [1.0, 2.0],
        }
    ).to_csv(src, index=False)
    with pytest.raises(ValueError, match="duplicate_keys"):
        ingest_archive_role("daily_basic", src, dest=tmp_path / "basic.parquet")


def test_validate_corporate_actions_warns_without_none_but_does_not_fill():
    df = normalize_panel(
        pd.DataFrame(
            {
                "trade_date": ["20240102"],
                "ts_code": ["000001.SZ"],
                "event": ["cash_dividend"],
            }
        )
    )
    report = validate_archive_frame(df, "corporate_actions")
    assert report["ok"] is True
    assert "corporate_action_missing_none_rows" in report["warnings"]
    assert df["corporate_action"].tolist() == ["cash_dividend"]


def test_auto_provider_uses_archive_when_file_exists(tmp_path):
    dest = tmp_path / "data/raw/providers/csi100_members.parquet"
    dest.parent.mkdir(parents=True)
    pd.DataFrame(
        {"trade_date": ["20240102"], "ts_code": ["000001.SZ"], "weight": [1.0]}
    ).to_parquet(dest, index=False)
    cfg = _cfg(tmp_path)
    assert resolve_evidence_provider("universe", cfg, tushare_token="") == "archive"
    assert resolve_evidence_provider("daily_basic", cfg, tushare_token="") is None


def test_auto_provider_prefers_tushare_token_over_archive(tmp_path):
    dest = tmp_path / "data/raw/providers/csi100_members.parquet"
    dest.parent.mkdir(parents=True)
    pd.DataFrame(
        {"trade_date": ["20240102"], "ts_code": ["000001.SZ"], "weight": [1.0]}
    ).to_parquet(dest, index=False)
    cfg = _cfg(tmp_path)
    assert resolve_evidence_provider("universe", cfg, tushare_token="tok") == "tushare"


def test_auto_provider_stays_unresolved_without_token_or_file(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_evidence_provider("universe", cfg, tushare_token="") is None
    assert resolve_evidence_provider("daily_basic", cfg, tushare_token="") is None


def test_rqdata_provider_is_archive_export_only(tmp_path):
    cfg = _cfg(tmp_path, providers={"universe": "rqdata"})
    with pytest.raises(RuntimeError, match="archive"):
        resolve_evidence_provider("universe", cfg, tushare_token="")


def test_validate_registered_archives_fail_closed_when_strict(tmp_path):
    cfg = _cfg(tmp_path)
    loose = validate_registered_archives(cfg, strict=False)
    assert loose["ok"] is True
    assert loose["n_ready"] == 0
    strict = validate_registered_archives(cfg, strict=True)
    assert strict["ok"] is False
    assert "universe:file_missing" in strict["issues"]


def test_akshare_cannot_satisfy_vendor_circ_mv_contract():
    out = AkshareAdapter().fetch_daily_basic("000001.SZ", "20240101", "20240131")
    assert out.empty
    assert "circ_mv" in out.columns
