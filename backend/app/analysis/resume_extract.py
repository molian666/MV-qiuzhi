"""简历文件文本提取：PDF / Word(docx) / 纯文本。

- .pdf  → pypdf 逐页取文本
- .docx → python-docx 取段落与表格
- .txt / .md → 直接读，UTF-8 严格解码失败回退 GBK（简历常来自 Windows 导出）
- 其他（.doc 老二进制、图片等）→ 明确拒绝，提示用户另存为 docx/pdf

提取后做轻量清洗：去掉只剩空白的行，折叠 2 行以上连续空行。
"""

from __future__ import annotations

from pathlib import Path

#: 支持上传的扩展名（小写、无点）。
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


class UnsupportedResumeFile(ValueError):
    """文件类型不支持，message 可直接展示给用户。"""


def _clean_text(raw: str) -> str:
    lines = [line.strip() for line in raw.splitlines()]
    cleaned: list[str] = []
    blank = 0
    for line in lines:
        if not line:
            blank += 1
            if blank <= 1:
                cleaned.append("")
            continue
        blank = 0
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _extract_docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        parts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def extract_resume_text(path: Path) -> str:
    """按扩展名提取简历正文；不支持的类型抛 UnsupportedResumeFile。"""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedResumeFile(
            f"暂不支持 {suffix or '该'} 格式：请另存为 PDF / Word(docx) / txt / md 后再上传"
        )
    try:
        if suffix == ".pdf":
            raw = _extract_pdf(path)
        elif suffix == ".docx":
            raw = _extract_docx(path)
        else:
            raw = _read_text(path)
    except Exception as exc:  # noqa: BLE001 - 解析库的异常形态各异，统一转成用户能懂的错误
        raise UnsupportedResumeFile(f"文件解析失败：{exc}") from exc
    return _clean_text(raw)
