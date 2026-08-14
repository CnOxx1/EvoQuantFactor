import pandas as pd

from qfactor.data.archive_adapter import ArchiveAdapter
from qfactor.data.dataset import overlay_execution_evidence
from qfactor.data.quality import check_daily_panel
from qfactor.eval.trading import execution_mask, simulate_non_overlapping_long_short


def test_archive_execution_evidence_and_quality_report(tmp_path):
    status = tmp_path / "security_status.csv"
    actions = tmp_path / "corporate_actions.csv"
    basic = tmp_path / "daily_basic.csv"
    pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03"],
            "ts_code": ["A", "A"],
            "is_st": [False, False],
            "is_suspended": [False, True],
            "limit_up": [110.0, 110.0],
            "limit_down": [90.0, 90.0],
        }
    ).to_csv(status, index=False)
    pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03"],
            "ts_code": ["A", "A"],
            "corporate_action": ["none", "cash_dividend"],
            "adj_factor_vendor": [1.0, 0.98],
        }
    ).to_csv(actions, index=False)
    pd.DataFrame(
        {
            "trade_date": ["2024-01-02", "2024-01-03"],
            "ts_code": ["A", "A"],
            "circ_mv": [100.0, 100.0],
            "turnover_rate": [1.0, 1.0],
            "free_float_shares": [10.0, 10.0],
            "adv_20d": [1_000.0, 1_000.0],
        }
    ).to_csv(basic, index=False)
    adapter = ArchiveAdapter(security_status=status, corporate_actions=actions, daily_basic=basic)
    panel = pd.DataFrame(
        {
            "trade_date": ["20240102", "20240103"],
            "ts_code": ["A", "A"],
            "close": [100.0, 100.0],
            "high": [110.0, 100.0],
            "low": [100.0, 90.0],
            "adv_20d": [1_000.0, 1_000.0],
            "free_float_shares": [10.0, 10.0],
        }
    )
    merged, info = overlay_execution_evidence(
        panel,
        adapter.fetch_security_status("20240102", "20240103"),
        adapter.fetch_corporate_actions("20240102", "20240103"),
        status_provider="archive",
        actions_provider="archive",
    )
    report = check_daily_panel(merged).to_dict()
    assert info["security_status_coverage"] == 1.0
    assert info["limit_price_coverage"] == 1.0
    assert report["suspension_rate_pct"] == 0.5
    assert report["limit_hit_rate_pct"] == 1.0
    assert report["adv_20d_coverage_pct"] == 1.0
    assert report["corporate_action_coverage_pct"] == 1.0


def test_execution_mask_requires_pit_status_limit_and_adv_evidence():
    dates = pd.Index(["20240102", "20240103"])
    codes = pd.Index(["A", "B"])
    open_px = pd.DataFrame([[100.0, 110.0], [100.0, 100.0]], index=dates, columns=codes)
    pre_close = pd.DataFrame(100.0, index=dates, columns=codes)
    is_st = pd.DataFrame(False, index=dates, columns=codes)
    is_suspended = pd.DataFrame([[False, False], [True, False]], index=dates, columns=codes)
    limit_up = pd.DataFrame(110.0, index=dates, columns=codes)
    limit_down = pd.DataFrame(90.0, index=dates, columns=codes)
    mask, meta = execution_mask(
        open_px,
        pre_close,
        is_st=is_st,
        is_suspended=is_suspended,
        limit_up=limit_up,
        limit_down=limit_down,
    )
    assert bool(mask.loc["20240102", "A"])
    assert not bool(mask.loc["20240102", "B"])
    assert not bool(mask.loc["20240103", "A"])
    assert meta["limit_mode"] == "pit_prices"
    assert meta["limit_price_coverage"] == 1.0

    signal = pd.DataFrame([[1.0, 2.0, 3.0, 4.0]] * 12, index=pd.date_range("2024-01-01", periods=12, freq="B").strftime("%Y%m%d"), columns=["A", "B", "C", "D"])
    px = pd.DataFrame(100.0, index=signal.index, columns=signal.columns)
    full_false = pd.DataFrame(False, index=signal.index, columns=signal.columns)
    full_up = pd.DataFrame(110.0, index=signal.index, columns=signal.columns)
    full_down = pd.DataFrame(90.0, index=signal.index, columns=signal.columns)
    adv = pd.DataFrame(1_000_000.0, index=signal.index, columns=signal.columns)
    out = simulate_non_overlapping_long_short(
        signal,
        px,
        px,
        px,
        is_st=full_false,
        is_suspended=full_false,
        limit_up=full_up,
        limit_down=full_down,
        adv_20d=adv,
        hold_days=3,
        quantiles=2,
        min_names_per_leg=2,
    )
    assert out["capacity_per_name_median"] == 50_000.0
    assert out["limitations"] == []
