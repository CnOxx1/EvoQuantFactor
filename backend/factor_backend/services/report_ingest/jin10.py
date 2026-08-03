from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FLASH_JS_URL = "https://www.jin10.com/flash_newest.js"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.jin10.com/",
}

_ARRAY_RE = re.compile(r"var\s+newest\s*=\s*(\[.*\])\s*;?\s*$", re.S)


class Jin10Client:
    """金十数据快讯（flash_newest.js）。"""

    def __init__(self, *, request_gap_sec: float = 1.0, timeout_sec: float = 30.0) -> None:
        self.request_gap_sec = max(0.3, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.request_gap_sec:
            time.sleep(self.request_gap_sec - elapsed)
        self._last_req = time.monotonic()

    def fetch_flash(self) -> list[dict[str, Any]]:
        self._throttle()
        with httpx.Client(timeout=self.timeout_sec, trust_env=False, headers=DEFAULT_HEADERS) as client:
            resp = client.get(FLASH_JS_URL)
            resp.raise_for_status()
            text = resp.text
        m = _ARRAY_RE.search(text.strip())
        if not m:
            # 宽松匹配
            m = re.search(r"(\[\s*\{[\s\S]*\}\s*\])", text)
        if not m:
            raise RuntimeError("jin10: cannot parse flash_newest.js")
        arr = json.loads(m.group(1))
        return [x for x in arr if isinstance(x, dict)]

    def iter_recent_items(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        items = self.fetch_flash()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            fid = str(it.get("id") or "").strip()
            if not fid or fid in seen:
                continue
            seen.add(fid)
            item = dict(it)
            item["_flash_id"] = fid
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            # 顶层补 title，供采集器列表入库使用（金十常把标题放在 data 里）
            item["title"] = self._resolve_title(data, fid)
            out.append(item)
            if len(out) >= page_size:
                break
        return out

    @staticmethod
    def _resolve_title(data: dict[str, Any], fid: str) -> str:
        title = str(data.get("title") or data.get("vip_title") or "").strip()
        content = str(data.get("content") or data.get("vip_desc") or "").strip()
        if title:
            return title[:200]
        # 金十很多快讯只有 content、无独立 title：用首句/首行作标题
        if content:
            # 去掉常见前缀噪音
            cleaned = re.sub(r"^金十数据\d{1,2}月\d{1,2}日讯[，,：:\s]*", "", content)
            first = re.split(r"[\n。！？!?]", cleaned or content, maxsplit=1)[0].strip()
            if not first:
                first = cleaned or content
            if len(first) > 80:
                first = first[:80].rstrip() + "…"
            return first
        return f"金十快讯 {fid}"

    def fetch_item_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        fid = str(item.get("_flash_id") or item.get("id") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        title = str(item.get("title") or "").strip() or self._resolve_title(data, fid)
        content = str(data.get("content") or "").strip()
        vip_locked = bool(data.get("lock")) or ("vip_title" in data and not content)
        if vip_locked and not content:
            content = str(data.get("vip_desc") or data.get("vip_title") or "（金十 VIP 内容，需登录会员查看全文）")
        publish = str(item.get("time") or "")
        important = bool(item.get("important"))
        url = f"https://www.jin10.com/flash/{fid}" if fid else "https://www.jin10.com/"
        meta = {
            "source": "jin10",
            "external_id": f"jin10:{fid}",
            "title": title,
            "org": "金十数据",
            "author": "金十数据",
            "publish_date": publish,
            "url": url,
            "q_type": 96,
            "q_type_label": "金十/快讯" + ("/重要" if important else "") + ("/VIP" if vip_locked else ""),
            "important": important,
            "vip_locked": vip_locked,
            "text_incomplete": vip_locked or len((content or title).strip()) < 20,
        }
        body = content or title
        parts = [
            f"标题：{title}",
            f"来源：金十数据",
            f"时间：{publish}" if publish else "",
            f"重要：{'是' if important else '否'}",
            f"链接：{url}",
            "",
            body,
        ]
        return "\n".join(p for p in parts if p is not None), meta
