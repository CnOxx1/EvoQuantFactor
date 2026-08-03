from __future__ import annotations

import hashlib
import re
from typing import Any


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def normalize_for_fingerprint(title: str, content: str) -> str:
    raw = f"{(title or '')[:100]}\n{(content or '')[:500]}"
    raw = _WS_RE.sub("", raw).lower()
    return _PUNCT_RE.sub("", raw)


def content_fingerprint(title: str, content: str) -> str:
    """跨源近似去重指纹（标题+正文前缀）。"""
    norm = normalize_for_fingerprint(title, content)
    if len(norm) < 12:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def is_bad_title(title: str | None, external_id: str | None = None) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if external_id and t == external_id:
        return True
    if re.match(r"^(jin10|ths|sina|wallstreetcn|luobo|eastmoney)[:_]", t, re.I):
        return True
    if re.fullmatch(r"金十快讯\s*\d+", t):
        return True
    if re.fullmatch(r"\d{14,}", t):
        return True
    return False


def _strip_html_bits(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


def title_from_stored_content(content: str, fallback: str = "") -> str:
    text = content or ""
    m = re.search(r"标题[：:]\s*(.+)", text)
    if m:
        cand = _strip_html_bits(m.group(1).strip())
        if cand and not is_bad_title(cand):
            return cand[:200]
    # 正文块：跳过元信息行
    body = text
    if "\n\n" in text:
        body = text.split("\n\n", 1)[-1]
    body = _strip_html_bits(body.strip())
    if not body:
        return fallback[:200] if fallback else ""
    cleaned = re.sub(r"^金十数据\d{1,2}月\d{1,2}日讯[，,：:\s]*", "", body)
    first = re.split(r"[\n。！？!?]", cleaned or body, maxsplit=1)[0].strip()
    # 正文仍是坏占位标题时放弃
    if not first or is_bad_title(first):
        return fallback[:200] if fallback else ""
    if len(first) > 80:
        first = first[:80].rstrip() + "…"
    return first


def attach_fingerprint(meta: dict[str, Any], title: str, content: str) -> dict[str, Any]:
    out = dict(meta or {})
    fp = content_fingerprint(title, content)
    if fp:
        out["content_fp"] = fp
    return out
