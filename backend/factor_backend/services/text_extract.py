from __future__ import annotations

from pathlib import Path


def decode_upload(filename: str, raw: bytes) -> str:
    name = (filename or "report.txt").lower()
    if name.endswith(".pdf"):
        return _read_pdf(raw)
    # try utf-8 then gbk
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
        import io

        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError("PDF 未提取到文本，请上传 txt/md 或可复制文本的 PDF")
        return text
    except ImportError as e:
        raise ValueError("未安装 pypdf，无法解析 PDF。请 pip install pypdf 或改传 txt/md") from e
