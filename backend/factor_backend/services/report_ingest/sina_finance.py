from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ROLL_URL = "https://feed.mix.sina.com.cn/api/roll/get"

# pageid/lid 组合：滚动新闻频道
DEFAULT_CHANNELS = [
    ("153", "2516", "财经滚动"),
    ("155", "1686", "证券滚动"),
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://finance.sina.com.cn/",
}


class SinaFinanceClient:
    """新浪财经滚动资讯。"""

    def __init__(self, *, request_gap_sec: float = 0.8, timeout_sec: float = 30.0) -> None:
        self.request_gap_sec = max(0.3, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.request_gap_sec:
            time.sleep(self.request_gap_sec - elapsed)
        self._last_req = time.monotonic()

    def fetch_roll(self, *, pageid: str, lid: str, num: int = 20) -> list[dict[str, Any]]:
        self._throttle()
        with httpx.Client(timeout=self.timeout_sec, trust_env=False, headers=DEFAULT_HEADERS) as client:
            resp = client.get(
                ROLL_URL,
                params={"pageid": pageid, "lid": lid, "num": str(num), "page": "1"},
            )
            resp.raise_for_status()
            data = resp.json()
        status = ((data.get("result") or {}).get("status")) or {}
        code = status.get("code")
        try:
            code_i = int(code) if code is not None else -1
        except (TypeError, ValueError):
            code_i = -1
        if code_i != 0:
            raise RuntimeError(f"sina roll: {status.get('msg')}")
        items = ((data.get("result") or {}).get("data")) or []
        return [x for x in items if isinstance(x, dict)]

    def iter_recent_items(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pageid, lid, label in DEFAULT_CHANNELS:
            try:
                items = self.fetch_roll(pageid=pageid, lid=lid, num=page_size)
            except Exception as e:  # noqa: BLE001
                logger.warning("sina channel %s failed: %s", label, e)
                continue
            for it in items:
                docid = str(it.get("docid") or it.get("url") or "").strip()
                if not docid or docid in seen:
                    continue
                seen.add(docid)
                item = dict(it)
                item["_doc_id"] = docid
                item["_channel_label"] = label
                out.append(item)
        return out

    @staticmethod
    def _ts(sec: Any) -> str:
        try:
            v = int(sec)
            return datetime.fromtimestamp(v, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return str(sec or "")

    def fetch_item_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        docid = str(item.get("_doc_id") or item.get("docid") or "")
        title = str(item.get("title") or f"新浪资讯 {docid}")
        summary = str(item.get("intro") or item.get("summary") or item.get("wapsummary") or "")
        media = str(item.get("media_name") or item.get("author") or "新浪财经")
        url = str(item.get("url") or "")
        publish = self._ts(item.get("ctime") or item.get("mtime"))
        label = str(item.get("_channel_label") or "财经")
        meta = {
            "source": "sina",
            "external_id": f"sina:{docid}",
            "org": media,
            "author": media,
            "publish_date": publish,
            "url": url,
            "q_type": 94,
            "q_type_label": f"新浪/{label}",
            "text_incomplete": len(summary.strip()) < 20,
        }
        parts = [
            f"标题：{title}",
            f"来源：{media}",
            f"时间：{publish}" if publish else "",
            f"链接：{url}" if url else "",
            "",
            summary or "（列表摘要为空，可打开链接查看全文）",
        ]
        return "\n".join(p for p in parts if p is not None), meta
