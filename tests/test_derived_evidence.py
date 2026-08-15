import json
from types import SimpleNamespace

import pandas as pd

from qfactor.data.dataset import DataService, fill_derived_adv


def test_fill_derived_adv_needs_twenty_positive_amounts():
    dates = [f"202401{i:02d}" for i in range(1, 22)]
    panel = pd.DataFrame(
        {
            "ts_code": ["AAA.SH"] * 21,
            "trade_date": dates,
            "amount": [5.0] * 21,
        }
    )
    out = fill_derived_adv(panel)
    assert int(out["adv_20d"].isna().sum()) == 19
    assert int(out["adv_20d"].notna().sum()) == 2
    assert abs(float(out.loc[19, "adv_20d"]) - 5.0) < 1e-12


def test_fill_derived_adv_ignores_nonpositive_amount():
    dates = [f"202401{i:02d}" for i in range(1, 22)]
    amount = [5.0] * 21
    amount[10] = 0.0
    panel = pd.DataFrame(
        {
            "ts_code": ["AAA.SH"] * 21,
            "trade_date": dates,
            "amount": amount,
        }
    )
    out = fill_derived_adv(panel)
    assert bool(out["adv_20d"].isna().all())


def test_enrich_derived_evidence_stamps_snapshot_and_fills_adv(tmp_path):
    dates = [f"202401{i:02d}" for i in range(1, 22)]
    bars = pd.DataFrame(
        {
            "ts_code": ["AAA.SH"] * 21,
            "trade_date": dates,
            "amount": [8.0] * 21,
        }
    )
    processed = tmp_path / "processed"
    bars_path = processed / "bars" / "daily" / "bars.parquet"
    bars_path.parent.mkdir(parents=True)
    bars.to_parquet(bars_path, index=False)
    meta_path = processed / "data_version.json"
    meta_path.write_text(
        json.dumps(
            {
                "limitations": [
                    "snapshot CSI100 constituents",
                    "circ_mv estimated from amount/turnover",
                ]
            }
        ),
        encoding="utf-8",
    )

    svc = object.__new__(DataService)
    svc.cfg = SimpleNamespace(path=lambda key: processed if key == "data_processed" else tmp_path)
    svc.bars_path = bars_path
    svc.load_bars = lambda: pd.read_parquet(bars_path)

    out = svc.enrich_derived_evidence()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    filled = pd.read_parquet(bars_path)

    assert out["enriched"] is True
    assert out["bars_rewritten"] is True
    assert out["universe_mode"] == "snapshot"
    assert out["circ_mv_source"] == "estimated"
    assert out["adv_20d_coverage"] > 0
    assert meta["universe_mode"] == "snapshot"
    assert meta["circ_mv_source"] == "estimated"
    assert meta["adv_20d_source"] == "rolling_completed_amount"
    assert meta["universe_mode"] != "pit"
    assert int(filled["adv_20d"].notna().sum()) == 2


def test_enrich_derived_evidence_does_not_upgrade_existing_pit_meta(tmp_path):
    processed = tmp_path / "processed"
    bars_path = processed / "bars" / "daily" / "bars.parquet"
    bars_path.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            "ts_code": ["AAA.SH"],
            "trade_date": ["20240102"],
            "amount": [1.0],
            "adv_20d": [1.0],
        }
    ).to_parquet(bars_path, index=False)
    meta_path = processed / "data_version.json"
    meta_path.write_text(
        json.dumps({"universe_mode": "pit", "circ_mv_source": "tushare_daily_basic"}),
        encoding="utf-8",
    )

    svc = object.__new__(DataService)
    svc.cfg = SimpleNamespace(path=lambda key: processed if key == "data_processed" else tmp_path)
    svc.bars_path = bars_path
    svc.load_bars = lambda: pd.read_parquet(bars_path)

    out = svc.enrich_derived_evidence()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert out["universe_mode"] == "pit"
    assert out["circ_mv_source"] == "tushare_daily_basic"
    assert meta["universe_mode"] == "pit"
    assert meta["circ_mv_source"] == "tushare_daily_basic"
