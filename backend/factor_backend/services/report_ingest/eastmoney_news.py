from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NEWS_LIST_URL = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"

# 东财栏目：财经导读 / 股市 / 公司 / 宏观 / 产经 / 国际
DEFAULT_COLUMNS = [
    (350, "财经导读"),
    (344, "股市"),
    (355, "公司"),
    (354, "宏观"),
    (351, "产经"),
    (353, "国际"),
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class EastmoneyNewsClient:
    """东方财富资讯栏目（非研报 PDF）。"""

    def __init__(self, *, request_gap_sec: float = 1.0, timeout_sec: float = 30.0) -> None:
        self.request_gap_sec = max(0.3, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.request_gap_sec:
            time.sleep(self.request_gap_sec - elapsed)
        self._last_req = time.monotonic()

    def fetch_column(
        self,
        column: int,
        *,
        page_index: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        params = {
            "client": "web",
            "biz": "web_news_col",
            "column": str(column),
            "page_index": str(page_index),
            "page_size": str(page_size),
            "req_trace": f"eqf_{column}_{page_index}",
        }
        self._throttle()
        with httpx.Client(timeout=self.timeout_sec, trust_env=False, headers=DEFAULT_HEADERS) as client:
            resp = client.get(NEWS_LIST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict) or str(data.get("code")) != "1":
            raise RuntimeError(f"eastmoney news column={column}: {data.get('message') if isinstance(data, dict) else data}")
        items = ((data.get("data") or {}).get("list")) or []
        return [x for x in items if isinstance(x, dict)]

    def iter_recent_items(
        self,
        *,
        columns: list[tuple[int, str]] | None = None,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        columns = columns or DEFAULT_COLUMNS
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for col_id, col_label in columns:
            try:
                items = self.fetch_column(col_id, page_index=1, page_size=page_size)
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney news column=%s failed: %s", col_id, e)
                continue
            for item in items:
                code = str(item.get("code") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                item = dict(item)
                item["_column"] = col_id
                item["_column_label"] = col_label
                item["_news_id"] = code
                out.append(item)
        return out

    def fetch_article_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        title = str(item.get("title") or "未命名资讯")
        summary = str(item.get("summary") or "")
        media = str(item.get("mediaName") or "")
        show_time = str(item.get("showTime") or "")
        url = str(item.get("uniqueUrl") or item.get("url") or "")
        if url.startswith("http://"):
            # 详情页 https 更稳
            url_https = "https://" + url[len("http://") :]
        else:
            url_https = url
        news_id = str(item.get("_news_id") or item.get("code") or "")
        col_label = str(item.get("_column_label") or "")
        meta = {
            "source": "eastmoney_news",
            "external_id": f"eastmoney_news:{news_id}",
            "news_id": news_id,
            "org": media,
            "author": media,
            "publish_date": show_time,
            "url": url_https or url,
            "q_type": 90,
            "q_type_label": f"资讯/{col_label}" if col_label else "资讯",
            "column": item.get("_column"),
            "column_label": col_label,
            "text_incomplete": False,
        }

        body = ""
        if url_https or url:
            try:
                body = self._fetch_article_body(url_https or url)
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney news body failed %s: %s", news_id, e)
                meta["body_error"] = str(e)[:200]

        if not (body or "").strip():
            meta["text_incomplete"] = True
            body = summary

        parts = [
            f"标题：{title}",
            f"来源：{media}" if media else "",
            f"时间：{show_time}" if show_time else "",
            f"栏目：{col_label}" if col_label else "",
            f"链接：{url_https or url}" if (url_https or url) else "",
            "",
            "【摘要】",
            summary or "（无）",
            "",
            "【正文】",
            body or "（无正文）",
        ]
        content = "\n".join(p for p in parts if p is not None)
        return content, meta

    def _fetch_article_body(self, url: str) -> str:
        self._throttle()
        with httpx.Client(
            timeout=self.timeout_sec,
            trust_env=False,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
        # 常见正文容器
        for pat in [
            r'<div[^>]+id="ContentBody"[^>]*>([\s\S]*?)</div>\s*<div[^>]+class="[^"]*em_media',
            r'<div[^>]+id="ContentBody"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]+class="[^"]*article-body[^"]*"[^>]*>([\s\S]*?)</div>',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                raw = m.group(1)
                text = _TAG_RE.sub("\n", raw)
                text = _WS_RE.sub(" ", text).replace(" \n", "\n").strip()
                if len(text) > 40:
                    return text[:20000]
        # meta description fallback
        m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.I)
        if m:
            return m.group(1).strip()
        return ""
