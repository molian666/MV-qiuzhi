"""简历附件的存放。

**后端只收图片、不收 PDF**（PDF 在前端用 pdf.js 转换，原文不出本机）。所以这里的测试
重点是"它不会把不该收的东西收下来"——把 PDF 当图片发给平台，投递会静默失败，
而那种失败在真机上极难定位。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.apply.attachment import (
    MAX_BYTES,
    AttachmentError,
    detect_image_suffix,
    safe_filename,
    store_attachment,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 32
#: WAV 也是 RIFF 开头，必须被排除（这正是只看 RIFF 会误判的例子）
WAV = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


# --------------------------------------------------------------------------- #
# 类型识别：按文件头，不看后缀
# --------------------------------------------------------------------------- #
def test_detects_supported_images() -> None:
    assert detect_image_suffix(PNG) == ".png"
    assert detect_image_suffix(JPG) == ".jpg"
    assert detect_image_suffix(GIF) == ".gif"
    assert detect_image_suffix(WEBP) == ".webp"


def test_rejects_pdf() -> None:
    """**最关键的一条**：PDF 不能当图片发出去。"""
    assert detect_image_suffix(PDF) == ""


def test_rejects_wav_despite_the_riff_header() -> None:
    """WAV 与 WebP 都是 RIFF 开头——只看前 4 字节会把 WAV 当成图片。"""
    assert detect_image_suffix(WAV) == ""


def test_rejects_empty_and_garbage() -> None:
    assert detect_image_suffix(b"") == ""
    assert detect_image_suffix(b"just some text") == ""


def test_rejects_pdf_even_when_named_png() -> None:
    """后缀是最好骗的东西：扩展名写成 .png 的 PDF 仍然要被拒。"""
    with pytest.raises(AttachmentError) as excinfo:
        store_attachment(Path("."), data=PDF, original_name="resume.png")
    assert "不是图片" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 存盘
# --------------------------------------------------------------------------- #
def test_stores_under_a_dedicated_folder(tmp_path: Path) -> None:
    stored = store_attachment(tmp_path, data=PNG, original_name="my-resume.png")

    assert stored.suffix == ".png"
    assert stored.size == len(PNG)
    # 与录音/文档分开，便于清理
    assert stored.relative_path.startswith("resume_images/")
    assert stored.absolute_path.is_file()
    assert stored.absolute_path.read_bytes() == PNG


def test_relative_path_is_relative_to_the_uploads_dir(tmp_path: Path) -> None:
    stored = store_attachment(tmp_path, data=PNG, original_name="a.png")
    # 入库的是相对路径：换机器/换数据目录时绝对路径会失效
    assert not Path(stored.relative_path).is_absolute()
    assert (tmp_path / stored.relative_path).is_file()


def test_task_id_is_used_as_a_prefix(tmp_path: Path) -> None:
    stored = store_attachment(tmp_path, data=PNG, task_id=42, original_name="cv.png")
    assert Path(stored.relative_path).name.startswith("task42_")


def test_missing_original_name_still_works(tmp_path: Path) -> None:
    stored = store_attachment(tmp_path, data=PNG)
    assert stored.absolute_path.is_file()


def test_empty_payload_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AttachmentError):
        store_attachment(tmp_path, data=b"")


def test_oversized_payload_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AttachmentError) as excinfo:
        store_attachment(tmp_path, data=PNG + b"\x00" * (MAX_BYTES + 1))
    assert "太大" in str(excinfo.value)


def test_rewriting_the_same_name_overwrites(tmp_path: Path) -> None:
    """同一个任务重传附件应当覆盖，而不是无限堆积文件。"""
    first = store_attachment(tmp_path, data=PNG, task_id=1, original_name="cv.png")
    second = store_attachment(tmp_path, data=JPG, task_id=1, original_name="cv.png")

    # 后缀不同因此是两个文件；同名同后缀时才是覆盖
    assert first.absolute_path != second.absolute_path

    again = store_attachment(tmp_path, data=PNG, task_id=1, original_name="cv.png")
    assert again.absolute_path == first.absolute_path


# --------------------------------------------------------------------------- #
# 文件名清洗
# --------------------------------------------------------------------------- #
def test_filename_strips_directory_traversal() -> None:
    """用户给的文件名不能带路径——否则可以写到数据目录之外。"""
    cleaned = safe_filename("../../etc/passwd")
    assert "/" not in cleaned
    assert ".." not in cleaned


def test_filename_strips_windows_paths() -> None:
    cleaned = safe_filename(r"C:\Users\me\resume.png")
    assert "\\" not in cleaned
    assert cleaned.endswith("resume.png")


def test_filename_keeps_chinese_characters() -> None:
    assert safe_filename("王工简历.png") == "王工简历.png"


def test_filename_is_length_limited() -> None:
    assert len(safe_filename("x" * 500 + ".png")) <= 60


def test_empty_filename_falls_back() -> None:
    assert safe_filename("") == "resume"
    assert safe_filename("   ") == "resume"


def test_stored_path_cannot_escape_the_uploads_dir(tmp_path: Path) -> None:
    """端到端的防线：带穿越意图的文件名最终仍落在 uploads 目录里。"""
    stored = store_attachment(tmp_path, data=PNG, original_name="../../../evil.png")

    resolved = stored.absolute_path.resolve()
    assert str(resolved).startswith(str(tmp_path.resolve()))
