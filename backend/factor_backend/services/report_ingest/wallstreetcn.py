from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LIVE_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
ARTICLE_URL = "https://api-one.wallstcn.com/apiv1/content/articles"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://wallstreetcn.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_TAG_RE = re.compile(r"<[^>]+>")


class WallstreetcnClient:
    """华尔街见闻：7x24 快讯 + 文章。"""

    def __init__(self, *, request_gap_sec: float = 0.8, timeout_sec: float = 30.0) -> None:
        self.request_gap_sec = max(0.3, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.request_gap_sec:
            time.sleep(self.request_gap_sec - elapsed)
        self._last_req = time.monotonic()

    def fetch_lives(self, *, limit: int = 20, channel: str = "global-channel") -> list[dict[str, Any]]:
        self._throttle()
        with httpx.Client(timeout=self.timeout_sec, trust_env=False, headers=DEFAULT_HEADERS) as client:
            resp = client.get(LIVE_URL, params={"channel": channel, "client": "pc", "limit": str(limit)})
            resp.raise_for_status()
            data = resp.json()
        if int(data.get("code") or 0) != 20000:
            raise RuntimeError(f"wallstreetcn lives: {data.get('message')}")
        items = ((data.get("data") or {}).get("items")) or []
        return [x for x in items if isinstance(x, dict)]

    def fetch_articles(self, *, limit: int = 20, channel: str = "global") -> list[dict[str, Any]]:
        self._throttle()
        with httpx.Client(timeout=self.timeout_sec, trust_env=False, headers=DEFAULT_HEADERS) as client:
            resp = client.get(
                ARTICLE_URL,
                params={"channel": channel, "accept": "article", "limit": str(limit)},
            )
            resp.raise_for_status()
            data = resp.json()
        if int(data.get("code") or 0) != 20000:
            raise RuntimeError(f"wallstreetcn articles: {data.get('message')}")
        items = ((data.get("data") or {}).get("items")) or []
        return [x for x in items if isinstance(x, dict)]

    def iter_recent_items(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for kind, fetcher in (("live", self.fetch_lives), ("article", self.fetch_articles)):
            try:
                items = fetcher(limit=page_size)
            except Exception as e:  # noqa: BLE001
                logger.warning("wallstreetcn %s failed: %s", kind, e)
                continue
            for it in items:
                iid = str(it.get("id") or "").strip()
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                item = dict(it)
                item["_kind"] = kind
                item["_item_id"] = iid
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
        kind = str(item.get("_kind") or item.get("type") or "live")
        iid = str(item.get("_item_id") or item.get("id") or "")
        author = ""
        a = item.get("author") or {}
        if isinstance(a, dict):
            author = str(a.get("display_name") or "")
        publish = self._ts(item.get("display_time"))
        if kind == "article":
            title = str(item.get("title") or item.get("source_name") or f"见闻文章 {iid}")
            summary = str(item.get("content_short") or "")
            url = str(item.get("uri") or f"https://wallstreetcn.com/articles/{iid}")
            body = summary
            label = "见闻/文章"
        else:
            title = str(item.get("title") or item.get("content_text") or f"见闻快讯 {iid}")
            if len(title) > 80:
                title = title[:80] + "…"
            summary = str(item.get("content_text") or "")
            more = str(item.get("content_more") or "")
            html = str(item.get("content") or "")
            body = summary or _TAG_RE.sub("", html)
            if more:
                body = f"{body}\n{more}".strip()
            url = str(item.get("uri") or f"https://wallstreetcn.com/livenews/{iid}")
            label = "见闻/7x24"

        meta = {
            "source": "wallstreetcn",
            "external_id": f"wallstreetcn:{kind}:{iid}",
            "org": author or "华尔街见闻",
            "author": author or "华尔街见闻",
            "publish_date": publish,
            "url": url,
            "q_type": 93,
            "q_type_label": label,
            "text_incomplete": len((body or "").strip()) < 20,
        }
        parts = [
            f"标题：{title}",
            f"来源：{meta['org']}",
            f"时间：{publish}" if publish else "",
            f"链接：{url}",
            "",
            body or summary or "（无正文）",
        ]
        return "\n".join(p for p in parts if p is not None), meta
