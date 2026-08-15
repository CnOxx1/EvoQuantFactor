from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from qfactor.data.archive_ingest import ingest_archive_role
from qfactor.data.csindex import CSINDEX_CONS_URL, CSINDEX_WEIGHT_URL, _code_to_ts, fetch_csindex_members
from qfactor.settings import ProjectConfig, get_project_config

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
SEARCH_QUERIES = (
    "关于调整沪深300和中证香港100等指数样本",
    "关于调整沪深300等指数样本",
    "关于调整沪深300和中证100",
)
SKIP_TITLE = ("精明", "月度指数")
CSI100_NAMES = {"中证100", "中证a100", "csi a100", "csi100"}
# Regular CSI reviews are ~6 months. A longer hole means missing official files;
# do not stitch older add/remove lists onto a later snapshot.
MAX_CHANGE_GAP_DAYS = 240


def _get(url: str, timeout: float = 45.0) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def is_csi100_index(code: object, name: object) -> bool:
    raw_code = str(code or "").strip()
    digits = "".join(ch for ch in raw_code if ch.isdigit())
    if digits.zfill(6) == "000903":
        return True
    nm = str(name or "").strip().lower().replace(" ", "")
    if not nm:
        return False
    if "1000" in nm or "香港" in nm or "hongkong" in nm:
        return False
    return nm in CSI100_NAMES or nm.replace("指数", "") in CSI100_NAMES


def _ts(code: object) -> str | None:
    raw = str(code or "").strip()
    if not raw or raw in {"-", "—", "nan", "None", "<NA>"}:
        return None
    if raw.endswith(".0") and raw[:-2].replace("-", "").isdigit():
        raw = raw[:-2]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    digits = digits.zfill(6)
    return _code_to_ts(digits[-6:])


def parse_adjustment_excel(content: bytes) -> dict[str, set[str]]:
    """Parse CSIndex 调入/调出 workbooks. Returns ts_code sets for 000903 only."""
    added: set[str] = set()
    removed: set[str] = set()
    xf = pd.ExcelFile(BytesIO(content))
    names = {str(s) for s in xf.sheet_names}

    def take(df: pd.DataFrame, bucket: set[str], code_col: str) -> None:
        if df is None or df.empty:
            return
        idx_col = next((c for c in df.columns if "指数代码" in str(c)), None)
        name_col = next((c for c in df.columns if "指数简称" in str(c) or "指数名称" in str(c)), None)
        if idx_col is None or code_col not in df.columns:
            return
        for _, row in df.iterrows():
            if is_csi100_index(row.get(idx_col), row.get(name_col) if name_col else None):
                code = _ts(row.get(code_col))
                if code:
                    bucket.add(code)

    if {"调入", "调出"} <= names:
        take(xf.parse("调入"), added, "证券代码")
        take(xf.parse("调出"), removed, "证券代码")
        return {"added": added, "removed": removed}

    # Recent one-sheet layout: 指数代码, 指数简称, 调出, <name>, 调入, <name>
    sheet = xf.parse(xf.sheet_names[0])
    cols = list(sheet.columns)
    if len(cols) >= 5 and "调出" in str(cols[2]) and "调入" in str(cols[4]):
        last_code, last_name = None, None
        for _, row in sheet.iterrows():
            code_v, name_v = row.iloc[0], row.iloc[1]
            if pd.notna(code_v) or pd.notna(name_v):
                last_code, last_name = code_v, name_v
            if not is_csi100_index(last_code, last_name):
                continue
            out_c = _ts(row.iloc[2])
            in_c = _ts(row.iloc[4])
            if out_c:
                removed.add(out_c)
            if in_c:
                added.add(in_c)
    return {"added": added, "removed": removed}


def effective_date_from_html(html: str, fallback: str) -> str:
    text = re.sub(r"<[^>]+>", "", html or "")
    found = re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if found:
        y, m, d = found[0]
        return f"{int(y):04d}{int(m):02d}{int(d):02d}"
    return str(fallback).replace("-", "")[:8]


def search_adjustment_notices() -> list[dict[str, Any]]:
    seen: dict[int, dict[str, Any]] = {}
    for q in SEARCH_QUERIES:
        url = (
            "https://www.csindex.com.cn/csindex-home/search/search-content"
            f"?lang=cn&searchInput={quote(q)}"
            "&pageNum=1&pageSize=100&sortField=date&dateRange=all&contentType=announcement"
        )
        payload = _get(url).json()
        for item in payload.get("data") or []:
            hid = int(item.get("id"))
            title = re.sub(r"</?b>", "", str(item.get("headline") or item.get("title") or ""))
            if any(s in title for s in SKIP_TITLE):
                continue
            seen[hid] = {
                "id": hid,
                "date": str(item.get("itemDate") or "")[:10],
                "title": title,
            }
    return sorted(seen.values(), key=lambda x: x["date"], reverse=True)


def fetch_notice_detail(notice_id: int) -> dict[str, Any]:
    url = f"https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id={notice_id}"
    return _get(url).json().get("data") or {}


def reconstruct_snapshots(
    latest: pd.DataFrame,
    changes: list[dict[str, Any]],
) -> tuple[pd.DataFrame, list[str]]:
    """Walk backwards from the official latest file. Stop at the first review gap."""
    notes: list[str] = []
    if latest is None or latest.empty:
        return pd.DataFrame(columns=["trade_date", "ts_code", "weight"]), ["latest_empty"]
    file_date = str(latest["trade_date"].iloc[0])
    current = set(latest["ts_code"].astype(str))
    rows = [
        {"trade_date": file_date, "ts_code": c, "weight": w}
        for c, w in zip(latest["ts_code"], latest.get("weight", pd.Series(dtype=object)))
    ]
    cursor = pd.Timestamp(file_date)
    usable = 0
    for ch in sorted(changes, key=lambda x: x["effective_date"], reverse=True):
        eff = pd.Timestamp(str(ch["effective_date"]))
        gap = (cursor - eff).days
        if gap > MAX_CHANGE_GAP_DAYS:
            notes.append(
                f"stopped_before_{ch['effective_date']}: gap_{gap}d_exceeds_{MAX_CHANGE_GAP_DAYS}d"
            )
            break
        added = set(ch.get("added") or [])
        removed = set(ch.get("removed") or [])
        if not added and not removed:
            continue
        # Membership after this change equals `current` only when no later
        # unknown review exists. The gap guard is what makes that claim honest.
        rows.extend(
            {"trade_date": eff.strftime("%Y%m%d"), "ts_code": c, "weight": pd.NA}
            for c in sorted(current)
        )
        current = (current - added) | removed
        cursor = eff
        usable += 1
    notes.append(f"applied_contiguous_changes={usable}")
    out = pd.DataFrame(rows).drop_duplicates(["trade_date", "ts_code"])
    return out, notes


def fetch_official_history(
    dest_root: Path | None = None,
    cfg: ProjectConfig | None = None,
    *,
    write_archive: bool = True,
) -> dict[str, Any]:
    """Download official CSIndex files and write a gap-safe universe archive.

    Does not invent Wind/Choice daily-basic or execution panels. Missing Jun/Dec
    review workbooks stop reconstruction instead of stitching across years.
    """
    cfg = cfg or get_project_config()
    root = dest_root or (cfg.root / "data" / "raw" / "providers")
    raw_dir = root / "csindex"
    notice_dir = raw_dir / "notices"
    raw_dir.mkdir(parents=True, exist_ok=True)
    notice_dir.mkdir(parents=True, exist_ok=True)

    latest = fetch_csindex_members("000903")
    for label, url in (
        ("000903cons.xls", CSINDEX_CONS_URL.format(code="000903")),
        ("000903closeweight.xls", CSINDEX_WEIGHT_URL.format(code="000903")),
    ):
        (raw_dir / label).write_bytes(_get(url).content)

    notices = search_adjustment_notices()
    changes: list[dict[str, Any]] = []
    skipped: list[str] = []
    for item in notices:
        try:
            detail = fetch_notice_detail(int(item["id"]))
        except Exception as e:
            skipped.append(f"{item['id']}:detail:{e}")
            continue
        enclosures = detail.get("enclosureList") or []
        if not enclosures:
            skipped.append(f"{item['id']}:no_excel")
            continue
        enc = enclosures[0]
        try:
            content = _get(str(enc["fileUrl"])).content
        except Exception as e:
            skipped.append(f"{item['id']}:download:{e}")
            continue
        suffix = Path(str(enc.get("fileName") or "notice.xlsx")).suffix or ".xlsx"
        (notice_dir / f"{item['date']}_{item['id']}{suffix}").write_bytes(content)
        parsed = parse_adjustment_excel(content)
        if not parsed["added"] and not parsed["removed"]:
            skipped.append(f"{item['id']}:no_000903_rows")
            continue
        eff = effective_date_from_html(str(detail.get("content") or ""), item["date"])
        changes.append(
            {
                "id": item["id"],
                "title": item["title"],
                "notice_date": item["date"],
                "effective_date": eff,
                "added": sorted(parsed["added"]),
                "removed": sorted(parsed["removed"]),
            }
        )

    members, notes = reconstruct_snapshots(latest, changes)
    events = pd.DataFrame(changes)
    if not events.empty:
        events.to_parquet(root / "csi100_reconstitution_events.parquet", index=False)
    members_path = root / "csi100_members.parquet"
    if write_archive and not members.empty:
        tmp = root / "_csi100_members_src.parquet"
        members.to_parquet(tmp, index=False)
        ingest_archive_role("universe", tmp, cfg=cfg, dest=members_path)
        tmp.unlink(missing_ok=True)

    provenance = {
        "provider": "csindex_official",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "latest_file_date": str(latest["trade_date"].iloc[0]) if not latest.empty else None,
        "n_latest": int(len(latest)),
        "n_notices_seen": len(notices),
        "n_changes_with_000903": len(changes),
        "n_member_snapshots": int(members["trade_date"].nunique()) if not members.empty else 0,
        "notes": notes,
        "skipped": skipped[:50],
        "warning": (
            "Official latest constituent file plus contiguous add/remove notices only. "
            "A missing Jun/Dec review workbook stops reconstruction. "
            "This is not a Wind/Choice daily-basic or execution archive."
        ),
    }
    (raw_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        **provenance,
        "members_path": str(members_path) if members_path.exists() else None,
        "raw_dir": str(raw_dir),
    }
