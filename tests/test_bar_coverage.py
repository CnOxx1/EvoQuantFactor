from pathlib import Path

import pandas as pd

from qfactor.data.dataset import codes_covering_window, extra_research_codes


def test_codes_covering_window_rejects_partial_history():
    panel = pd.DataFrame(
        {
            "ts_code": ["AAA.SH", "AAA.SH", "BBB.SH", "BBB.SH"],
            "trade_date": ["20240102", "20260630", "20200102", "20260814"],
        }
    )
    have = codes_covering_window(panel, "20200102", "20260814")
    assert have == {"BBB.SH"}


def test_codes_covering_window_empty_panel():
    assert codes_covering_window(pd.DataFrame(), "20200102", "20251231") == set()


def test_extra_research_codes_reads_reconstitution_events(tmp_path, monkeypatch):
    from qfactor.settings import get_project_config

    root = tmp_path
    dest = root / "data" / "raw" / "providers"
    dest.mkdir(parents=True)
    pd.DataFrame(
        {
            "added": [["002028.SZ"], ["300750.SZ"]],
            "removed": [["601989.SH"], ["000876.SZ"]],
        }
    ).to_parquet(dest / "csi100_reconstitution_events.parquet", index=False)
    cfg = get_project_config()
    monkeypatch.setattr(cfg, "root", Path(root))
    assert extra_research_codes(cfg) == [
        "000876.SZ",
        "002028.SZ",
        "300750.SZ",
        "601989.SH",
    ]
