from io import BytesIO

import pandas as pd

from qfactor.data.csindex_history import (
    effective_date_from_html,
    is_csi100_index,
    parse_adjustment_excel,
    reconstruct_snapshots,
)


def _xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()


def test_is_csi100_index_excludes_csi1000_and_hk():
    assert is_csi100_index("000903", "中证A100")
    assert is_csi100_index(903, "中证100")
    assert not is_csi100_index("000852", "中证1000")
    assert not is_csi100_index("H11164", "中证香港100")


def test_parse_two_sheet_adjustment_keeps_only_000903():
    content = _xlsx(
        {
            "调入": pd.DataFrame(
                {
                    "指数代码": ["000903", "000852"],
                    "指数简称": ["中证100", "中证1000"],
                    "证券代码": ["002028", "000048"],
                    "证券简称": ["思源电气", "京基智农"],
                }
            ),
            "调出": pd.DataFrame(
                {
                    "指数代码": ["000903", "000852"],
                    "指数简称": ["中证100", "中证1000"],
                    "证券代码": ["601989", "000038"],
                    "证券简称": ["中国重工", "深大通"],
                }
            ),
        }
    )
    out = parse_adjustment_excel(content)
    assert out["added"] == {"002028.SZ"}
    assert out["removed"] == {"601989.SH"}


def test_parse_one_sheet_adjustment():
    content = _xlsx(
        {
            "Sheet1": pd.DataFrame(
                {
                    "指数代码": ["000903"],
                    "指数简称": ["中证A100"],
                    "调出": ["601989"],
                    "Unnamed: 3": ["中国重工"],
                    "调入": ["002028"],
                    "Unnamed: 5": ["思源电气"],
                }
            )
        }
    )
    out = parse_adjustment_excel(content)
    assert out["added"] == {"002028.SZ"}
    assert out["removed"] == {"601989.SH"}


def test_reconstruct_stops_at_review_gap():
    latest = pd.DataFrame(
        {
            "trade_date": ["20260814", "20260814"],
            "ts_code": ["AAA.SH", "BBB.SZ"],
            "weight": [1.0, 2.0],
        }
    )
    changes = [
        {
            "effective_date": "20260616",
            "added": ["BBB.SZ"],
            "removed": ["CCC.SH"],
        },
        {
            "effective_date": "20211213",
            "added": ["AAA.SH"],
            "removed": ["DDD.SH"],
        },
    ]
    members, notes = reconstruct_snapshots(latest, changes)
    dates = set(members["trade_date"])
    assert "20260814" in dates
    assert "20260616" in dates
    assert "20211213" not in dates
    assert any("stopped_before_20211213" in n for n in notes)
    post = set(members.loc[members["trade_date"] == "20260616", "ts_code"])
    assert post == {"AAA.SH", "BBB.SZ"}


def test_effective_date_from_html():
    html = "<p>自2021年12月10日收市后生效。</p>"
    assert effective_date_from_html(html, "2021-11-26") == "20211210"
