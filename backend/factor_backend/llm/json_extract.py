from __future__ import annotations

import json
import re
from typing import Any


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|JSON)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def slice_json_blob(text: str) -> str:
    """截取第一个完整 JSON 对象/数组片段（括号配对，忽略字符串内括号）。"""
    start_obj = text.find("{")
    start_arr = text.find("[")
    if start_obj < 0 and start_arr < 0:
        return text
    if start_obj < 0:
        start = start_arr
        open_ch, close_ch = "[", "]"
    elif start_arr < 0:
        start = start_obj
        open_ch, close_ch = "{", "}"
    else:
        if start_obj < start_arr:
            start = start_obj
            open_ch, close_ch = "{", "}"
        else:
            start = start_arr
            open_ch, close_ch = "[", "]"

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def repair_common_json_issues(text: str) -> str:
    """修复模型常见非法 JSON：尾逗号、智能引号、注释、BOM。"""
    text = text.lstrip("\ufeff").strip()
    # 智能引号 → 标准引号
    text = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    # 去掉 // 行注释与 /* */ 块注释（粗略，足够覆盖多数模型输出）
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    text = re.sub(r"(^|[^:])//.*?$", r"\1", text, flags=re.MULTILINE)
    # 尾逗号： ,} 或 ,]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    # Python/JS 风格 True/False/None
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    return text.strip()


def _escape_raw_newlines_in_strings(text: str) -> str:
    """将 JSON 字符串字面量中的裸换行转义为 \\n。"""
    out: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_str = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def extract_json(text: str) -> Any:
    """从模型输出中尽量解析出 JSON；失败时抛出带上下文的 JSONDecodeError。"""
    original = text
    text = strip_code_fences(text)
    blobs = []
    sliced = slice_json_blob(text)
    if sliced:
        blobs.append(sliced)
    if text and text not in blobs:
        blobs.append(text)

    last_err: json.JSONDecodeError | None = None
    for blob in blobs:
        variants = [
            blob,
            repair_common_json_issues(blob),
            _escape_raw_newlines_in_strings(blob),
            _escape_raw_newlines_in_strings(repair_common_json_issues(blob)),
        ]
        seen: set[str] = set()
        for variant in variants:
            if not variant or variant in seen:
                continue
            seen.add(variant)
            try:
                return json.loads(variant)
            except json.JSONDecodeError as e:
                last_err = e
                continue

    if last_err is None:
        raise json.JSONDecodeError("No JSON object found", original or "", 0)

    # 附带出错附近片段，便于日志排查
    pos = last_err.pos or 0
    src = last_err.doc or ""
    lo = max(0, pos - 80)
    hi = min(len(src), pos + 80)
    snippet = src[lo:hi].replace("\n", "\\n")
    raise json.JSONDecodeError(
        f"{last_err.msg} (near ...{snippet}...)",
        src,
        pos,
    ) from last_err
