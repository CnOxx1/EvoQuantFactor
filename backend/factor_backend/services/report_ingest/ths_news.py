from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NEWS_URL = "https://news.10jqka.com.cn/tapp/news/push/stock/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://news.10jqka.com.cn/",
}


class ThsNewsClient:
    """同花顺资讯快讯。"""

    def __init__(self, *, request_gap_sec: float = 0.8, timeout_sec: float = 30.0) -> None:
        self.request_gap_sec = max(0.3, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.request_gap_sec:
            time.sleep(self.request_gap_sec - elapsed)
        self._last_req = time.monotonic()

    def fetch_page(self, *, page: int = 1, tag: str = "") -> list[dict[str, Any]]:
        self._throttle()
        params = {"page": str(page), "tag": tag, "track": "website"}
        with httpx.Client(timeout=self.timeout_sec, trust_env=False, headers=DEFAULT_HEADERS) as client:
            resp = client.get(NEWS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        if str(data.get("code")) != "200":
            raise RuntimeError(f"ths news: {data.get('msg')}")
        items = ((data.get("data") or {}).get("list")) or []
        return [x for x in items if isinstance(x, dict)]

    def iter_recent_items(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        # API 按页返回，取第一页即可；page_size 用于截断
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tag in ("", "重要"):
            try:
                items = self.fetch_page(page=1, tag=tag)
            except Exception as e:  # noqa: BLE001
                logger.warning("ths news tag=%r failed: %s", tag, e)
                continue
            for it in items:
                nid = str(it.get("seq") or it.get("id") or "").strip()
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                item = dict(it)
                item["_news_id"] = nid
                item["_tag_filter"] = tag or "全部"
                out.append(item)
                if len(out) >= page_size * 2:
                    return out
        return out

    @staticmethod
    def _ts(sec: Any) -> str:
        try:
            v = int(sec)
            return datetime.fromtimestamp(v, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return str(sec or "")

    def fetch_item_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        nid = str(item.get("_news_id") or item.get("seq") or item.get("id") or "")
        title = str(item.get("title") or f"同花顺资讯 {nid}")
        digest = str(item.get("digest") or item.get("short") or "")
        url = str(item.get("url") or item.get("shareUrl") or "")
        source = str(item.get("source") or "同花顺")
        publish = self._ts(item.get("ctime") or item.get("rtime"))
        tags = []
        for t in item.get("tags") or []:
            if isinstance(t, dict) and t.get("name"):
                tags.append(str(t["name"]))
        stocks = []
        for s in item.get("stock") or []:
            if isinstance(s, dict) and s.get("name"):
                code = s.get("stockCode") or ""
                stocks.append(f"{s['name']}({code})" if code else str(s["name"]))
        meta = {
            "source": "ths",
            "external_id": f"ths:{nid}",
            "org": source,
            "author": source,
            "publish_date": publish,
            "url": url,
            "q_type": 95,
            "q_type_label": "同花顺/快讯",
            "tags": tags,
            "related_stocks": stocks,
            "text_incomplete": len(digest.strip()) < 20,
        }
        parts = [
            f"标题：{title}",
            f"来源：{source}",
            f"时间：{publish}" if publish else "",
            f"标签：{'、'.join(tags)}" if tags else "",
            f"相关：{'、'.join(stocks)}" if stocks else "",
            f"链接：{url}" if url else "",
            "",
            digest or "（无摘要）",
        ]
        return "\n".join(p for p in parts if p is not None), meta
