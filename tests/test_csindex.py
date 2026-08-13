from qfactor.data.csindex import fetch_csindex_members


def test_csindex_members_live():
    df = fetch_csindex_members("000903")
    assert len(df) >= 90
    assert "ts_code" in df.columns
    assert df["ts_code"].str.endswith(".SH").any() or df["ts_code"].str.endswith(".SZ").any()
    assert df["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ)").all()
