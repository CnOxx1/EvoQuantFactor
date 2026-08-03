from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

FEED_LIST_URL = "https://gw.datayes.com/rrp_mammon/web/feed/list"
FEED_DETAIL_URL = "https://gw.datayes.com/rrp_mammon/web/feed"
REPORT_LIST_URL = "https://gw.datayes.com/rrp_adventure/web/reportList"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://robo.datayes.com/",
    "Origin": "https://robo.datayes.com",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class LuoboAuthError(RuntimeError):
    """萝卜投研未登录或 token 失效。"""


class LuoboClient:
    """萝卜投研（通联 DataYes / robo.datayes.com）客户端。

    公开接口需登录。请在环境变量配置：
    - LUOBO_CLOUD_SSO_TOKEN：浏览器 Cookie 中的 cloud-sso-token
    - 或 LUOBO_COOKIE：完整 Cookie 字符串
    """

    def __init__(
        self,
        *,
        cloud_sso_token: str = "",
        cookie: str = "",
        request_gap_sec: float = 1.2,
        timeout_sec: float = 45.0,
    ) -> None:
        self.cloud_sso_token = (cloud_sso_token or "").strip()
        self.cookie = (cookie or "").strip()
        self.request_gap_sec = max(0.4, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

    def configured(self) -> bool:
        return bool(self.cloud_sso_token or self.cookie)

    def _cookies(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.cookie:
            for part in self.cookie.split(";"):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                k, v = part.split("=", 1)
                out[k.strip()] = unquote(v.strip())
        if self.cloud_sso_token:
            out["cloud-sso-token"] = self.cloud_sso_token
        return out

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.request_gap_sec:
            time.sleep(self.request_gap_sec - elapsed)
        self._last_req = time.monotonic()

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_sec,
            trust_env=False,
            headers=DEFAULT_HEADERS,
            cookies=self._cookies(),
            follow_redirects=True,
        )

    def _ensure_ok(self, data: Any, *, context: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise RuntimeError(f"luobo {context}: unexpected response")
        code = data.get("code")
        msg = str(data.get("message") or "")
        if code in (-403, -407) or "login" in msg.lower() or "登录" in msg:
            raise LuoboAuthError(f"luobo {context}: Need login（请配置 LUOBO_CLOUD_SSO_TOKEN）")
        if msg != "success" and str(code) not in ("0", "1", "200"):
            # 部分接口 success 时 code=0 / message=success
            if data.get("data") is None:
                raise RuntimeError(f"luobo {context}: code={code} message={msg}")
        return data

    def fetch_feed_list(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        if not self.configured():
            raise LuoboAuthError("LUOBO_CLOUD_SSO_TOKEN / LUOBO_COOKIE 未配置")
        self._throttle()
        with self._client() as client:
            resp = client.get(FEED_LIST_URL, params={"pageSize": str(page_size)})
            resp.raise_for_status()
            data = self._ensure_ok(resp.json(), context="feed/list")
        payload = data.get("data") or {}
        items = payload.get("list") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for it in items:
            if isinstance(it, dict) and it.get("id") is not None:
                out.append(it)
        return out

    def fetch_feed_detail(self, feed_id: str | int) -> dict[str, Any]:
        self._throttle()
        with self._client() as client:
            resp = client.get(FEED_DETAIL_URL, params={"id": str(feed_id)})
            resp.raise_for_status()
            data = self._ensure_ok(resp.json(), context="feed/detail")
        payload = data.get("data")
        return payload if isinstance(payload, dict) else {}

    def fetch_report_list(self, *, page_now: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        if not self.configured():
            raise LuoboAuthError("LUOBO_CLOUD_SSO_TOKEN / LUOBO_COOKIE 未配置")
        self._throttle()
        with self._client() as client:
            resp = client.get(
                REPORT_LIST_URL,
                params={"pageNow": str(page_now), "pageSize": str(page_size)},
            )
            resp.raise_for_status()
            data = self._ensure_ok(resp.json(), context="reportList")
        payload = data.get("data") or {}
        items = (
            payload.get("list")
            or payload.get("records")
            or payload.get("data")
            or (payload if isinstance(payload, list) else [])
        )
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict)]

    def iter_recent_feeds(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        items = self.fetch_feed_list(page_size=page_size)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            fid = str(it.get("id") or "").strip()
            if not fid or fid in seen:
                continue
            seen.add(fid)
            item = dict(it)
            item["_feed_id"] = fid
            item["_kind"] = "feed"
            out.append(item)
        return out

    def iter_recent_reports(self, *, page_size: int = 20) -> list[dict[str, Any]]:
        items = self.fetch_report_list(page_size=page_size)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            rid = str(it.get("id") or it.get("reportId") or it.get("report_id") or "").strip()
            if not rid or rid in seen:
                continue
            seen.add(rid)
            item = dict(it)
            item["_report_id"] = rid
            item["_kind"] = "report"
            out.append(item)
        return out

    @staticmethod
    def _html_to_text(html: str) -> str:
        text = _TAG_RE.sub("\n", html or "")
        text = _WS_RE.sub(" ", text).replace(" \n", "\n").strip()
        return text

    @staticmethod
    def _ms_to_str(ms: Any) -> str:
        try:
            v = int(ms)
            if v > 10_000_000_000:  # ms
                v = v // 1000
            return datetime.fromtimestamp(v, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return str(ms or "")

    def fetch_feed_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        feed_id = str(item.get("_feed_id") or item.get("id") or "")
        title = str(item.get("title") or f"萝卜资讯 {feed_id}")
        column = ""
        col = item.get("roboColumn") or {}
        if isinstance(col, dict):
            column = str(col.get("name") or "")
        publish = self._ms_to_str(item.get("publishTime") or item.get("insertTime"))
        related = []
        for x in item.get("related") or []:
            if isinstance(x, dict) and x.get("targetName"):
                related.append(str(x["targetName"]))

        body = ""
        detail_err = None
        try:
            detail = self.fetch_feed_detail(feed_id)
            body = str(
                detail.get("longDocContent")
                or detail.get("content")
                or detail.get("summary")
                or ""
            )
            if "<" in body and ">" in body:
                body = self._html_to_text(body)
            if detail.get("title"):
                title = str(detail.get("title"))
        except Exception as e:  # noqa: BLE001
            detail_err = str(e)[:200]
            logger.warning("luobo feed detail failed %s: %s", feed_id, e)

        meta = {
            "source": "luobo",
            "external_id": f"luobo:feed:{feed_id}",
            "feed_id": feed_id,
            "org": column or "萝卜投研",
            "author": column or "萝卜投研",
            "publish_date": publish,
            "url": f"https://robo.datayes.com/v2/web/feed/{feed_id}",
            "q_type": 91,
            "q_type_label": f"萝卜/{column}" if column else "萝卜资讯",
            "related_stocks": related,
            "text_incomplete": not bool((body or "").strip()),
        }
        if detail_err:
            meta["body_error"] = detail_err

        summary = str(item.get("summary") or item.get("brief") or "")
        parts = [
            f"标题：{title}",
            f"栏目：{column}" if column else "",
            f"时间：{publish}" if publish else "",
            f"相关：{'、'.join(related)}" if related else "",
            f"链接：{meta['url']}",
            "",
            "【摘要】",
            summary or "（无）",
            "",
            "【正文】",
            body or summary or "（无正文；可能需有效登录态）",
        ]
        return "\n".join(p for p in parts if p is not None), meta

    def fetch_report_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rid = str(item.get("_report_id") or item.get("id") or "")
        title = str(item.get("title") or item.get("reportTitle") or f"萝卜研报 {rid}")
        org = str(item.get("orgName") or item.get("brokerName") or item.get("authorOrg") or "萝卜投研")
        author = str(item.get("author") or item.get("researcher") or "")
        if isinstance(item.get("author"), list):
            author = "、".join(str(x) for x in item["author"] if x)
        publish = str(
            item.get("publishTime")
            or item.get("publishDate")
            or item.get("publish_time")
            or self._ms_to_str(item.get("publishTimeStamp"))
        )
        summary = str(item.get("summary") or item.get("abstract") or item.get("content") or "")
        pdf_url = str(item.get("pdfUrl") or item.get("pdf_url") or item.get("fileUrl") or "")
        page_url = str(item.get("url") or item.get("reportUrl") or f"https://robo.datayes.com/v2/fastreport?id={rid}")

        meta = {
            "source": "luobo",
            "external_id": f"luobo:report:{rid}",
            "report_id": rid,
            "org": org,
            "author": author,
            "publish_date": publish,
            "pdf_url": pdf_url or None,
            "url": page_url,
            "q_type": 92,
            "q_type_label": "萝卜研报",
            "text_incomplete": not bool(summary.strip()),
        }

        # 尝试下载 PDF（若有直链）
        content = ""
        if pdf_url:
            try:
                self._throttle()
                with self._client() as client:
                    resp = client.get(pdf_url)
                    resp.raise_for_status()
                    raw = resp.content
                if raw.startswith(b"%PDF"):
                    from factor_backend.services.text_extract import decode_upload

                    content = decode_upload(f"luobo_{rid}.pdf", raw)
                    meta["text_incomplete"] = False
                    meta["pdf_bytes"] = len(raw)
                else:
                    meta["pdf_error"] = "pdf endpoint returned non-PDF"
            except Exception as e:  # noqa: BLE001
                meta["pdf_error"] = str(e)[:240]
                logger.warning("luobo pdf failed %s: %s", rid, e)

        if not (content or "").strip():
            parts = [
                f"标题：{title}",
                f"机构：{org}" if org else "",
                f"作者：{author}" if author else "",
                f"日期：{publish}" if publish else "",
                f"链接：{page_url}",
                f"PDF：{pdf_url}" if pdf_url else "",
                "",
                "【摘要/正文】",
                summary or "（无摘要；完整 PDF 可能需会员权限）",
            ]
            content = "\n".join(p for p in parts if p is not None)
            if not summary:
                meta["text_incomplete"] = True
        return content, meta
