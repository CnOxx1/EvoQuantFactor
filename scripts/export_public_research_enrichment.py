#!/usr/bin/env python3
"""Build public-source research-tier enrichments without relaxing production gates."""
from __future__ import annotations

import argparse
import json
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


def rows(result) -> pd.DataFrame:
    values: list[list[str]] = []
    while result.error_code == "0" and result.next():
        values.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {result.error_code} {result.error_msg}")
    return pd.DataFrame(values, columns=result.fields)


def derived_risk(bars: pd.DataFrame) -> pd.DataFrame:
    panel = bars.copy()
    panel["trade_date"] = panel["trade_date"].astype(str).str.replace("-", "", regex=False)
    panel["ret_1d"] = pd.to_numeric(panel["ret_1d"], errors="coerce")
    panel = panel.dropna(subset=["ret_1d"]).sort_values(["trade_date", "ts_code"])
    market = panel.groupby("trade_date")["ret_1d"].mean().rename("market_return")
    panel = panel.join(market, on="trade_date").sort_values(["ts_code", "trade_date"])
    grouped = panel.groupby("ts_code", group_keys=False)
    panel["volatility_60d"] = grouped["ret_1d"].transform(
        lambda series: series.rolling(60, min_periods=40).std(ddof=1) * np.sqrt(252.0)
    )
    panel["market_beta_60d"] = grouped.apply(
        lambda frame: frame["ret_1d"].rolling(60, min_periods=40).cov(frame["market_return"])
        / frame["market_return"].rolling(60, min_periods=40).var(ddof=1).replace(0, np.nan),
        include_groups=False,
    ).reset_index(level=0, drop=True)
    return panel[["trade_date", "ts_code", "volatility_60d", "market_beta_60d"]].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", default="data/processed/bars/daily/bars.parquet")
    parser.add_argument("--start", default="20240102")
    parser.add_argument("--end", default="20260630")
    parser.add_argument("--output-dir", default="data/raw/research/public_enrichment")
    args = parser.parse_args()
    bars = pd.read_parquet(args.bars)
    bars["trade_date"] = bars["trade_date"].astype(str).str.replace("-", "", regex=False)
    bars = bars.loc[bars["trade_date"].between(args.start, args.end)].copy()
    codes = sorted(bars["ts_code"].astype(str).unique())
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    actions: list[pd.DataFrame] = []
    industry_rows: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    try:
        for seq, ts_code in enumerate(codes, start=1):
            print(f"[{seq}/{len(codes)}] {ts_code}", flush=True)
            try:
                raw = rows(bs.query_history_k_data_plus(to_bs_code(ts_code), "date,close", ymd(args.start), ymd(args.end), frequency="d", adjustflag="3"))
                adjusted = rows(bs.query_history_k_data_plus(to_bs_code(ts_code), "date,close", ymd(args.start), ymd(args.end), frequency="d", adjustflag="2"))
                if not raw.empty and not adjusted.empty:
                    merged = raw.rename(columns={"date": "trade_date", "close": "raw_close"}).merge(
                        adjusted.rename(columns={"date": "trade_date", "close": "adjusted_close"}), on="trade_date", how="inner"
                    )
                    merged["raw_close"] = pd.to_numeric(merged["raw_close"], errors="coerce")
                    merged["adjusted_close"] = pd.to_numeric(merged["adjusted_close"], errors="coerce")
                    merged["derived_adjustment_factor"] = merged["adjusted_close"] / merged["raw_close"].replace(0, np.nan)
                    # A raw equality check would turn independent public rounding into
                    # daily fake events. Keep only material (>10bp) factor steps.
                    merged["factor_relative_change"] = merged["derived_adjustment_factor"].pct_change().abs()
                    merged["corporate_action"] = np.where(
                        merged["factor_relative_change"].gt(0.001),
                        "derived_adjustment_factor_change",
                        pd.NA,
                    )
                    merged["ts_code"] = ts_code
                    merged["trade_date"] = merged["trade_date"].str.replace("-", "", regex=False)
                    actions.append(merged[["trade_date", "ts_code", "corporate_action", "derived_adjustment_factor"]])
                industry = rows(bs.query_stock_industry(code=to_bs_code(ts_code)))
                if not industry.empty:
                    latest = industry.iloc[-1]
                    industry_rows.append({"ts_code": ts_code, "industry": str(latest.iloc[3]) if len(latest) > 3 else "", "industry_source": str(latest.iloc[4]) if len(latest) > 4 else "baostock_public"})
            except Exception as exc:
                failures.append({"ts_code": ts_code, "error": str(exc)})
    finally:
        bs.logout()

    action_frame = pd.concat(actions, ignore_index=True) if actions else pd.DataFrame(columns=["trade_date", "ts_code", "corporate_action", "derived_adjustment_factor"])
    action_frame.to_parquet(output / "corporate_actions_derived_research.parquet", index=False)
    industries = pd.DataFrame(industry_rows).drop_duplicates("ts_code")
    dates = pd.DataFrame({"trade_date": sorted(bars["trade_date"].unique())})
    industry_history = dates.merge(industries, how="cross") if not industries.empty else pd.DataFrame(columns=["trade_date", "ts_code", "industry", "industry_source"])
    industry_history.to_parquet(output / "industry_static_research.parquet", index=False)
    risks = derived_risk(bars)
    risks.to_parquet(output / "risk_exposures_internal_research.parquet", index=False)
    provenance = {
        "source": "BaoStock public API plus internally derived exposures",
        "exported_at": datetime.now(UTC).isoformat(),
        "coverage_window": {"start": args.start, "end": args.end},
        "codes_requested": len(codes),
        "corporate_action_rows": int(len(action_frame)),
        "derived_action_event_rows": int(action_frame["corporate_action"].notna().sum()),
        "industry_rows": int(len(industry_history)),
        "risk_exposure_rows": int(len(risks)),
        "failures": failures,
        "evidence_tier": "research_only",
        "production_limitations": [
            "Corporate actions are inferred from public adjusted/raw price ratios, not an official event ledger.",
            "Industry classifications are a current snapshot replicated over history and are not PIT.",
            "Risk exposures are transparent internal rolling estimates on a non-PIT research universe.",
            "No output may be used to pass a PIT, corporate-action, industry, risk, or release contract.",
        ],
    }
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(provenance, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
