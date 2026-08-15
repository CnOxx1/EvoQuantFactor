#!/usr/bin/env python3
"""Download the current official CSI A100 sample and weight snapshots.

This script intentionally writes only to ``data/raw/research``: an as-of file
is not a point-in-time historical constituent series and must never be used to
pass the production PIT-universe contract.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

INDEX_CODE = "000903"
BASE_URL = "https://www.csindex.com.cn/csindex-home"
OUTPUT_DIR = Path("data/raw/research/csindex_official_snapshot")


def fetch(url: str) -> bytes:
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "EvoQuantFactor/1.0 research archive"},
    )
    response.raise_for_status()
    return response.content


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metadata_url = f"{BASE_URL}/indexInfo/index-details-data?fileLang=2&indexCode={INDEX_CODE}"
    metadata = requests.get(metadata_url, timeout=30).json()
    if not metadata.get("success"):
        raise RuntimeError(f"official metadata request failed: {metadata}")

    manifest: dict[str, object] = {
        "source": "China Securities Index official public API",
        "index_code": INDEX_CODE,
        "fetched_at": fetched_at,
        "metadata_url": metadata_url,
        "evidence_tier": "official_current_snapshot_only",
        "production_limitations": [
            "This is an as-of download and not a historical PIT constituent reconstruction.",
            "Do not use it to fill earlier trade dates or to pass the PIT universe contract.",
        ],
        "files": {},
    }
    files = metadata["data"]
    for category in ("样本列表", "样本权重"):
        entries = files.get(category) or []
        if not entries:
            continue
        entry = entries[0]
        body = fetch(entry["filePath"])
        suffix = Path(entry["fileName"]).suffix or ".xls"
        stem = f"{INDEX_CODE}_{'constituents' if category == '样本列表' else 'weights'}"
        target = OUTPUT_DIR / f"{stem}{suffix}"
        target.write_bytes(body)
        parsed = pd.read_excel(BytesIO(body))
        parsed.to_parquet(OUTPUT_DIR / f"{stem}.parquet", index=False)
        manifest["files"][category] = {
            "source_url": entry["filePath"],
            "local_file": str(target),
            "sha256": hashlib.sha256(body).hexdigest(),
            "rows": int(len(parsed)),
            "columns": [str(column) for column in parsed.columns],
        }

    (OUTPUT_DIR / "provenance.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
