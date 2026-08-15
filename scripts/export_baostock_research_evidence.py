#!/usr/bin/env python3
"""Export auditable *research-tier* execution evidence from BaoStock.

This exporter deliberately does not write to ``data/raw/providers``. BaoStock's
public endpoint supplies historical turnover, trade status and ST flags, but it
does not provide PIT CSI100 membership nor vendor-certified daily limit prices.
The output is therefore useful for data reconciliation and provider comparison,
not sufficient for a production release contract.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import baostock as bs
import numpy as np
import pandas as pd


def to_bs_code(ts_code: str) -> str:
    code, market = ts_code.split(".")
    return f"{'sh' if market == 'SH' else 'sz'}.{code}"


def ymd(value: str) -> str:
    value = value.replace("-", "")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def query_rows(rs) -> pd.DataFrame:
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {rs.error_code} {rs.error_msg}")
    return pd.DataFrame(rows, columns=rs.fields)


def input_codes(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        frame = pd.read_sql_query(
            "SELECT DISTINCT ts_code FROM daily_bars ORDER BY ts_code", conn
        )
    return frame["ts_code"].astype(str).tolist()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/qfactor.sqlite3")
    parser.add_argument("--start", default="20240102")
    parser.add_argument("--end", default="20260630")
    parser.add_argument("--output-dir", default="data/raw/research/baostock_execution")
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = input_codes(db_path)
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")

    daily_rows: list[pd.DataFrame] = []
    status_rows: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    try:
        for index, ts_code in enumerate(codes, start=1):
            print(f"[{index}/{len(codes)}] {ts_code}", flush=True)
            try:
                rs = bs.query_history_k_data_plus(
                    to_bs_code(ts_code),
                    "date,code,close,volume,amount,turn,tradestatus,isST",
                    start_date=ymd(args.start),
                    end_date=ymd(args.end),
                    frequency="d",
                    adjustflag="3",
                )
                raw = query_rows(rs)
                if raw.empty:
                    continue
                raw["trade_date"] = raw["date"].str.replace("-", "", regex=False)
                raw["ts_code"] = ts_code
                raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
                raw["volume"] = pd.to_numeric(raw["volume"], errors="coerce")
                raw["amount"] = pd.to_numeric(raw["amount"], errors="coerce")
                raw["turnover_rate"] = pd.to_numeric(raw["turn"], errors="coerce")
                # BaoStock volume is shares and turn is percent. Derived free-float
                # shares are retained for reconciliation, never claimed as vendor-certified.
                raw["free_float_shares"] = raw["volume"] / (raw["turnover_rate"] / 100.0).replace(0, np.nan)
                raw["circ_mv"] = raw["close"] * raw["free_float_shares"] / 10000.0
                raw["amount_k_rmb"] = raw["amount"] / 1000.0
                raw["adv_20d"] = raw["amount_k_rmb"].rolling(20, min_periods=20).mean()
                daily_rows.append(raw[["trade_date", "ts_code", "turnover_rate", "free_float_shares", "circ_mv", "adv_20d"]])
                status_rows.append(
                    pd.DataFrame(
                        {
                            "trade_date": raw["trade_date"],
                            "ts_code": ts_code,
                            "is_st": raw["isST"].eq("1"),
                            "is_suspended": ~raw["tradestatus"].eq("1"),
                            # BaoStock does not provide explicit daily limit prices.
                            # Preserve nulls rather than applying board/ST heuristics.
                            "limit_up": np.nan,
                            "limit_down": np.nan,
                            "source": "baostock_public",
                        }
                    )
                )
            except Exception as exc:  # per-symbol faults must not discard evidence
                failures.append({"ts_code": ts_code, "error": str(exc)})
    finally:
        bs.logout()

    daily = pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()
    status = pd.concat(status_rows, ignore_index=True) if status_rows else pd.DataFrame()
    daily.to_parquet(out_dir / "daily_basic_baostock_research.parquet", index=False)
    status.to_parquet(out_dir / "security_status_baostock_research.parquet", index=False)
    provenance = {
        "source": "BaoStock public Python API",
        "source_url": "https://pypi.org/project/baostock/",
        "exported_at": datetime.now(UTC).isoformat(),
        "coverage_window": {"start": args.start, "end": args.end},
        "codes_requested": len(codes),
        "daily_basic_rows": int(len(daily)),
        "security_status_rows": int(len(status)),
        "failures": failures,
        "evidence_tier": "research_only",
        "production_limitations": [
            "No PIT CSI100 constituent history",
            "No explicit daily limit_up or limit_down prices",
            "Derived free_float_shares and circ_mv are not vendor-certified",
            "No point-in-time industry or risk exposure history",
            "No complete corporate-action event ledger",
        ],
    }
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
