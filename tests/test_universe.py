import pandas as pd
import pytest

from qfactor.data.universe import (
    build_universe_mask,
    freeze_at_start,
    members_from_in_out,
    resolve_universe,
    universe_stats,
)
from qfactor.settings import get_project_config


def _hist(*rows: tuple[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"trade_date": d, "ts_code": c, "weight": 1.0} for d, c in rows]
    )


def test_asof_mask_switches_at_reconstitution():
    members = _hist(
        ("20240102", "AAA.SH"),
        ("20240102", "BBB.SH"),
        ("20240617", "BBB.SH"),
        ("20240617", "CCC.SH"),
    )
    dates = ["20240102", "20240614", "20240617", "20241231"]
    codes = ["AAA.SH", "BBB.SH", "CCC.SH"]
    mask = build_universe_mask(dates, codes, members)
    assert bool(mask.loc["20240102", "AAA.SH"]) is True
    assert bool(mask.loc["20240614", "AAA.SH"]) is True
    assert bool(mask.loc["20240614", "CCC.SH"]) is False
    assert bool(mask.loc["20240617", "AAA.SH"]) is False
    assert bool(mask.loc["20240617", "CCC.SH"]) is True
    assert bool(mask.loc["20241231", "BBB.SH"]) is True


def test_freeze_at_start_ignores_later_entrants():
    hist = _hist(
        ("20231229", "OLD.SH"),
        ("20231229", "KEEP.SH"),
        ("20240617", "NEW.SH"),
        ("20240617", "KEEP.SH"),
    )
    frozen = freeze_at_start(hist, "20240102")
    assert set(frozen["ts_code"]) == {"OLD.SH", "KEEP.SH"}
    assert set(frozen["trade_date"]) == {"20240102"}
    assert "NEW.SH" not in set(frozen["ts_code"])


def test_members_from_in_out_drops_leavers():
    roster = pd.DataFrame(
        {
            "ts_code": ["STAY.SH", "LEAVE.SH", "JOIN.SH"],
            "in_date": ["20200101", "20200101", "20240617"],
            "out_date": [None, "20240617", None],
        }
    )
    snaps = members_from_in_out(roster, "20240102", "20241231")
    jan = set(snaps.loc[snaps["trade_date"] == "20240102", "ts_code"])
    jun = set(snaps.loc[snaps["trade_date"] == "20240617", "ts_code"])
    assert jan == {"STAY.SH", "LEAVE.SH"}
    assert "LEAVE.SH" not in jun
    assert "JOIN.SH" in jun
    assert "STAY.SH" in jun


def test_resolve_pit_keeps_multiple_snapshots(tmp_path):
    hist = _hist(
        ("20231229", "A.SH"),
        ("20231229", "B.SH"),
        ("20240617", "B.SH"),
        ("20240617", "C.SH"),
    )
    members, meta = resolve_universe(
        start="20240102",
        end="20241231",
        history=hist,
        cfg=get_project_config(),
    )
    assert meta["universe_mode"] == "pit"
    assert meta["n_snapshots"] >= 2
    assert meta["n_codes_union"] == 3
    assert set(members["ts_code"]) == {"A.SH", "B.SH", "C.SH"}


def test_resolve_freeze_start_locks_basket():
    hist = _hist(
        ("20231229", "A.SH"),
        ("20240617", "B.SH"),
    )
    cfg = get_project_config()
    original = dict(cfg.project)
    cfg.project["universe_policy"] = {"mode": "freeze_start", "lookback_days": 120}
    try:
        members, meta = resolve_universe(
            start="20240102", end="20241231", history=hist, cfg=cfg
        )
    finally:
        cfg.project.clear()
        cfg.project.update(original)
    assert meta["universe_mode"] == "freeze_start"
    assert set(members["ts_code"]) == {"A.SH"}
    assert universe_stats(members)["n_snapshots"] == 1


def test_resolve_pit_without_history_raises():
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        resolve_universe(
            start="20240102",
            end="20241231",
            history=pd.DataFrame(),
            cfg=get_project_config(),
        )


def test_snapshot_mode_is_opt_in():
    latest = _hist(("20260812", "TODAY.SH"))
    cfg = get_project_config()
    original = dict(cfg.project)
    cfg.project["universe_policy"] = {"mode": "snapshot", "lookback_days": 120}
    try:
        members, meta = resolve_universe(
            start="20240102",
            end="20260630",
            history=None,
            latest_snapshot=latest,
            cfg=cfg,
        )
    finally:
        cfg.project.clear()
        cfg.project.update(original)
    assert meta["universe_mode"] == "snapshot"
    assert set(members["ts_code"]) == {"TODAY.SH"}
    assert set(members["trade_date"]) == {"20240102"}


def test_overlay_daily_basic_prefers_vendor_circ_mv():
    from qfactor.data.dataset import overlay_daily_basic

    panel = pd.DataFrame(
        {
            "trade_date": ["20240102", "20240103"],
            "ts_code": ["AAA.SH", "AAA.SH"],
            "circ_mv": [1.0, 1.0],
            "turnover_rate": [2.0, 2.0],
        }
    )
    basic = pd.DataFrame(
        {
            "trade_date": ["20240102"],
            "ts_code": ["AAA.SH"],
            "circ_mv": [9.0],
            "turnover_rate": [4.0],
        }
    )
    out, info = overlay_daily_basic(panel, basic)
    assert abs(float(out.loc[0, "circ_mv"]) - 9.0) < 1e-12
    assert abs(float(out.loc[1, "circ_mv"]) - 1.0) < 1e-12
    assert info["circ_mv_source"] == "tushare_daily_basic"
    assert info["daily_basic_coverage"] == 0.5
