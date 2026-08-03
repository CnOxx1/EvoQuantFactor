from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from factor_backend.services.text_extract import decode_upload

logger = logging.getLogger(__name__)

LIST_URL = "https://reportapi.eastmoney.com/report/list"
JG_URL = "https://reportapi.eastmoney.com/report/jg"
# 注意：https 易触发 EO_Bot JS 挑战页（Content-Type 仍标 pdf）；http 可直下真实 PDF
PDF_URL_TMPL_HTTP = "http://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
PDF_URL_TMPL_HTTPS = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

QTYPE_LABELS = {
    0: "个股",
    1: "行业",
    2: "策略",
    3: "宏观",
    4: "晨报",
}

# 策略/宏观/晨报用 /jg；个股/行业用 /list
JG_QTYPES = {2, 3, 4}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/report/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # 避免 brotli；与浏览器差异尽量小
    "Accept-Encoding": "gzip, deflate",
}

_INFO_CODE_RE = re.compile(r"(AP\d{12,})")
_PDF_IN_PAGE_RE = re.compile(r"pdf\.dfcfw\.com/pdf/(H3_[A-Za-z0-9]+_1\.pdf)", re.I)


class EastmoneyReportClient:
    """东方财富研报公开接口客户端（列表 + PDF）。"""

    def __init__(self, *, request_gap_sec: float = 1.5, timeout_sec: float = 60.0) -> None:
        self.request_gap_sec = max(0.5, float(request_gap_sec))
        self.timeout_sec = float(timeout_sec)
        self._last_req = 0.0

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
            follow_redirects=True,
        )

    @staticmethod
    def pdf_urls(info_code: str) -> list[str]:
        code = (info_code or "").strip()
        if not code:
            return []
        # http 优先（实测可绕过 https 的 JS challenge）
        return [
            PDF_URL_TMPL_HTTP.format(info_code=code),
            PDF_URL_TMPL_HTTPS.format(info_code=code),
        ]

    def fetch_list(
        self,
        *,
        q_type: int = 1,
        page_no: int = 1,
        page_size: int = 20,
        begin_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if not end_time:
            end_time = f"{now.year + 1}-01-01"
        if not begin_time:
            begin_time = (now - timedelta(days=3)).strftime("%Y-%m-%d")

        if int(q_type) in JG_QTYPES:
            url = JG_URL
            params = {
                "pageSize": str(page_size),
                "beginTime": begin_time,
                "endTime": end_time,
                "pageNo": str(page_no),
                "fields": "",
                "qType": str(q_type),
                "orgCode": "",
                "p": str(page_no),
            }
        else:
            url = LIST_URL
            params = {
                "industryCode": "*",
                "pageSize": str(page_size),
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": begin_time,
                "endTime": end_time,
                "pageNo": str(page_no),
                "fields": "",
                "qType": str(q_type),
                "orgCode": "",
                "code": "",
                "rcode": "",
                "p": str(page_no),
                "pageNum": str(page_no),
                "pageNumber": str(page_no),
            }

        self._throttle()
        with self._client() as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError("eastmoney list: unexpected response")
        return data

    def _normalize_item(self, item: dict[str, Any], q_type: int) -> dict[str, Any] | None:
        item = dict(item)
        encode_url = str(item.get("encodeUrl") or "").strip()
        info_code = str(item.get("infoCode") or "").strip()
        # /jg 列表只有数字 id，需从详情页解析真实 AP infoCode
        if not info_code.startswith("AP") and encode_url:
            resolved = self.resolve_info_code(q_type, encode_url)
            if resolved:
                info_code = resolved
        if not info_code:
            # 仍无 AP 码则用 id/encodeUrl 作去重键，但 PDF 可能失败
            info_code = str(item.get("id") or encode_url or "").strip()
        if not info_code:
            return None
        item["_q_type"] = q_type
        item["_q_type_label"] = QTYPE_LABELS.get(q_type, str(q_type))
        item["_info_code"] = info_code
        item["_pdf_url"] = PDF_URL_TMPL_HTTP.format(info_code=info_code) if info_code.startswith("AP") else ""
        item["encodeUrl"] = encode_url
        return item

    def resolve_info_code(self, q_type: int, encode_url: str) -> str:
        """从详情页解析真实 AP infoCode。"""
        templates = {
            0: f"https://data.eastmoney.com/report/info/{quote(encode_url)}.html",
            1: f"https://data.eastmoney.com/report/zw_industry.jshtml?encodeUrl={quote(encode_url)}",
            2: f"https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl={quote(encode_url)}",
            3: f"https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={quote(encode_url)}",
            4: f"https://data.eastmoney.com/report/zw_morning.jshtml?encodeUrl={quote(encode_url)}",
        }
        url = templates.get(int(q_type)) or templates[1]
        try:
            self._throttle()
            with self._client() as client:
                html = client.get(url).text
            m = _INFO_CODE_RE.search(html)
            if m:
                return m.group(1)
            m2 = _PDF_IN_PAGE_RE.search(html)
            if m2:
                name = m2.group(1)
                # H3_AP...._1.pdf
                mm = _INFO_CODE_RE.search(name)
                if mm:
                    return mm.group(1)
        except Exception as e:  # noqa: BLE001
            logger.debug("resolve infoCode failed: %s", e)
        return ""

    def iter_recent_items(
        self,
        *,
        q_types: list[int],
        page_size: int = 20,
        lookback_hours: int = 24,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        begin = (now - timedelta(hours=max(1, lookback_hours))).strftime("%Y-%m-%d")
        end = f"{now.year + 1}-01-01"
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for qt in q_types:
            try:
                raw = self.fetch_list(q_type=qt, page_no=1, page_size=page_size, begin_time=begin, end_time=end)
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney list qType=%s failed: %s", qt, e)
                continue
            data = raw.get("data")
            if data is None:
                data = raw.get("Data") or raw.get("list") or []
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    norm = self._normalize_item(item, qt)
                except Exception as e:  # noqa: BLE001
                    logger.warning("normalize item failed qType=%s: %s", qt, e)
                    continue
                if not norm:
                    continue
                info_code = str(norm.get("_info_code") or "")
                if not info_code or info_code in seen:
                    continue
                seen.add(info_code)
                out.append(norm)
        return out

    def download_pdf_bytes(self, info_code: str) -> bytes:
        last_err: Exception | None = None
        for url in self.pdf_urls(info_code):
            try:
                self._throttle()
                with self._client() as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    raw = resp.content
                if raw.startswith(b"%PDF"):
                    return raw
                # https 常见：200 + application/pdf 但正文是 <script> challenge
                preview = raw[:80].decode("utf-8", errors="ignore")
                last_err = RuntimeError(f"non-PDF from {url} ({preview[:60]})")
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        if last_err:
            raise last_err
        raise RuntimeError(f"pdf download failed: {info_code}")

    def fetch_report_text(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """下载 PDF 并抽文本；失败则用标题/摘要/详情页描述占位。"""
        info_code = str(item.get("_info_code") or item.get("infoCode") or "").strip()
        title = str(item.get("title") or item.get("Title") or info_code or "未命名研报")
        org = str(item.get("orgSName") or item.get("orgName") or item.get("org") or "")
        author = str(item.get("researcher") or item.get("author") or "")
        if isinstance(item.get("author"), list):
            author = "、".join(str(x).split(".")[-1] for x in item["author"] if x)
        publish = str(item.get("publishDate") or item.get("datetime") or item.get("publishTime") or "")
        summary = str(item.get("summary") or item.get("content") or item.get("abs") or item.get("notice") or "")
        pdf_url = str(item.get("_pdf_url") or (self.pdf_urls(info_code)[0] if info_code else ""))
        q_type = int(item.get("_q_type") or 0)
        encode_url = str(item.get("encodeUrl") or "")
        meta_extra = {
            "source": "eastmoney",
            "external_id": f"eastmoney:{info_code}",
            "info_code": info_code,
            "org": org,
            "author": author,
            "publish_date": publish,
            "pdf_url": pdf_url,
            "encode_url": encode_url,
            "q_type": q_type,
            "q_type_label": item.get("_q_type_label") or QTYPE_LABELS.get(q_type, str(q_type)),
            "text_incomplete": False,
        }

        content = ""
        if info_code.startswith("AP"):
            try:
                raw = self.download_pdf_bytes(info_code)
                content = decode_upload(f"{info_code}.pdf", raw)
                meta_extra["pdf_bytes"] = len(raw)
            except Exception as e:  # noqa: BLE001
                logger.warning("eastmoney pdf extract failed %s: %s", info_code, e)
                meta_extra["pdf_error"] = str(e)[:300]

        if not (content or "").strip() and encode_url:
            page_summary = self._fetch_page_description(q_type, encode_url, info_code)
            if page_summary:
                summary = summary or page_summary

        if not (content or "").strip():
            meta_extra["text_incomplete"] = True
            parts = [
                f"标题：{title}",
                f"机构：{org}" if org else "",
                f"作者：{author}" if author else "",
                f"日期：{publish}" if publish else "",
                f"类型：{meta_extra['q_type_label']}",
                f"PDF：{pdf_url}",
                "",
                "【正文提取失败，以下为列表/详情摘要占位；可点「重抓 PDF」或人工上传】",
                summary or "（无摘要）",
            ]
            content = "\n".join(p for p in parts if p is not None)
        return content, meta_extra

    def _fetch_page_description(self, q_type: int, encode_url: str, info_code: str) -> str:
        templates = {
            0: f"https://data.eastmoney.com/report/info/{info_code}.html",
            1: f"https://data.eastmoney.com/report/zw_industry.jshtml?encodeUrl={quote(encode_url)}",
            2: f"https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl={quote(encode_url)}",
            3: f"https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={quote(encode_url)}",
            4: f"https://data.eastmoney.com/report/zw_morning.jshtml?encodeUrl={quote(encode_url)}",
        }
        url = templates.get(q_type) or templates[1]
        try:
            self._throttle()
            with self._client() as client:
                html = client.get(url).text
            m = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html, re.I)
            if m:
                return m.group(1).strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("page description failed: %s", e)
        return ""
