"""简历附件（图片简历）的存放。

**为什么只收图片、不收 PDF**：把 PDF 转成图片需要 PyMuPDF / pdfium 这类库，而本项目有一条
明确纪律"不加重量级依赖"。PLAN §6.7 给的做法更省事也更安全——**在前端用 pdf.js 转换，
图片不出本机**，后端只负责把一张已经转好的图片存下来。于是：

- 后端零新依赖；
- 简历原文（PDF）从头到尾没有离开用户的浏览器；
- 平台看到的也确实是图片简历（Boss 的图片简历就是这样）。

放在 `apply/` 而不是 `services`：它只服务于投递，且要能被"投递执行台"直接调用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: 只接受这几种图片。**按文件头判断，不信任文件名后缀**——
#: 后缀是最容易骗人的东西，而"把 PDF 当图片发给平台"会让投递静默失败。
#: `(文件头, 后缀, 额外校验)`：WebP 的 `RIFF` 太宽松（WAV 也是 RIFF），所以要再看第 8-12 字节。
_SIGNATURES: tuple[tuple[bytes, str, bytes | None], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", None),
    (b"\xff\xd8\xff", ".jpg", None),
    (b"GIF87a", ".gif", None),
    (b"GIF89a", ".gif", None),
    (b"RIFF", ".webp", b"WEBP"),
)

#: 单张图片的大小上限（图片简历就是给人看的，超过这个尺寸说明没压缩好）
MAX_BYTES = 8 * 1024 * 1024

_UNSAFE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")


class AttachmentError(RuntimeError):
    """附件不合法（不是图片 / 太大 / 任务不存在）。"""


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    """存好之后的附件信息。`relative_path` 是相对数据目录的路径（入库用这个）。"""

    relative_path: str
    absolute_path: Path
    size: int
    suffix: str


def detect_image_suffix(data: bytes) -> str:
    """按文件头判断图片类型；不是图片返回空串。"""
    for header, suffix, extra in _SIGNATURES:
        if not data.startswith(header):
            continue
        if extra is not None and data[8:12] != extra:
            continue
        return suffix
    return ""


def safe_filename(name: str, *, fallback: str = "resume") -> str:
    """把用户提供的文件名洗成安全的片段（去掉路径分隔符与奇怪字符）。"""
    base = Path(name or "").name or fallback
    cleaned = _UNSAFE.sub("_", base).strip("._") or fallback
    # 长度限制：文件系统对单段名有上限，而且这里也不需要长名字
    return cleaned[:60]


def store_attachment(
    uploads_dir: Path,
    *,
    data: bytes,
    task_id: int | None = None,
    original_name: str = "",
) -> StoredAttachment:
    """把一张图片存进 `uploads_dir`，返回它的相对路径。

    目录按 `resume_images/` 分开放，与其它上传物（录音、文档）混在一起会很难清理。
    """
    if not data:
        raise AttachmentError("附件是空的")

    suffix = detect_image_suffix(data)
    if not suffix:
        raise AttachmentError(
            "这个文件不是图片。图片简历请在前端用「转换并附加」生成 PNG/JPG；"
            "如果直接从平台下载了 PDF，请先转成图片再上传。"
        )

    if len(data) > MAX_BYTES:
        raise AttachmentError(f"附件太大（{len(data) // 1024} KB），请压缩到 {MAX_BYTES // 1024 // 1024} MB 以内")

    folder = uploads_dir / "resume_images"
    folder.mkdir(parents=True, exist_ok=True)

    stem = safe_filename(original_name, fallback="resume")
    # 用 task_id 前缀便于人肉排查"这张图是哪条任务的"；没有 task_id 就用时间戳避免撞名
    prefix = f"task{task_id}_" if task_id else ""
    target = folder / f"{prefix}{stem}{suffix}"

    target.write_bytes(data)
    relative = target.relative_to(uploads_dir).as_posix()
    return StoredAttachment(
        relative_path=relative,
        absolute_path=target,
        size=len(data),
        suffix=suffix,
    )
