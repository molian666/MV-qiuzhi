"""简历附件上传端点的端到端。

存放逻辑的单元测试在 `test_apply_attachment.py`；这里验证 HTTP 层的接线：
拒绝 PDF、能挂到任务上、以及**文件名穿越不能写到数据目录之外**。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from app.apply.runner import search_and_queue
from app.db import session_scope
from app.models.apply import ApplyTask

UPLOAD_URL = "/api/apply/attachments"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


def _upload(client: Any, *, data: bytes, filename: str, task_id: int | None = None) -> Any:
    files = {"file": (filename, data, "image/png")}
    form = {"task_id": str(task_id)} if task_id is not None else {}
    return client.post(UPLOAD_URL, files=files, data=form)


def test_upload_accepts_a_png(client: Any) -> None:
    response = _upload(client, data=PNG, filename="resume.png")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["path"].startswith("resume_images/")
    assert payload["size"] == len(PNG)


def test_upload_rejects_a_pdf_with_a_clear_reason(client: Any) -> None:
    """**关键**：把 PDF 当图片发给平台会让投递静默失败，所以要在入口就拒绝。"""
    response = _upload(client, data=PDF, filename="resume.pdf")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "不是图片" in detail
    # 报错要告诉用户正确的做法
    assert "PNG" in detail or "图片" in detail


def test_upload_rejects_a_pdf_renamed_to_png(client: Any) -> None:
    """后缀是最好骗的：扩展名改了也仍然要按**内容**判。"""
    response = _upload(client, data=PDF, filename="resume.png")

    assert response.status_code == 400


def test_upload_rejects_empty_file(client: Any) -> None:
    response = _upload(client, data=b"", filename="empty.png")

    assert response.status_code == 400


def test_upload_attaches_to_a_task(client: Any) -> None:
    with session_scope() as db:
        task = ApplyTask(status="approved")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    response = _upload(client, data=PNG, filename="cv.png", task_id=task_id)
    assert response.status_code == 200

    with session_scope() as db:
        saved = db.get(ApplyTask, task_id)

    assert saved is not None
    assert saved.resume_image_path == response.json()["path"]


def test_upload_for_a_missing_task_is_rejected(client: Any) -> None:
    response = _upload(client, data=PNG, filename="cv.png", task_id=999999)

    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


def test_upload_without_task_id_just_stores(client: Any) -> None:
    response = _upload(client, data=PNG, filename="cv.png")

    assert response.status_code == 200
    assert response.json()["task_id"] is None


def test_filename_traversal_cannot_escape(client: Any) -> None:
    """**安全**：带 `../` 的文件名不能写到数据目录之外。"""
    from app.config import get_settings

    response = _upload(client, data=PNG, filename="../../../../evil.png")

    assert response.status_code == 200, response.text
    path = response.json()["path"]
    assert ".." not in path
    assert path.startswith("resume_images/")

    resolved = (get_settings().upload_dir / path).resolve()
    assert str(resolved).startswith(str(get_settings().upload_dir.resolve()))


def test_uploaded_file_actually_exists_on_disk(client: Any) -> None:
    from app.config import get_settings

    response = _upload(client, data=PNG, filename="cv.png")
    target = get_settings().upload_dir / response.json()["path"]

    assert target.is_file()
    assert target.read_bytes() == PNG


# --------------------------------------------------------------------------- #
# 挂载状态要能在队列里看到
# --------------------------------------------------------------------------- #
def test_queue_exposes_the_attachment_path(client: Any) -> None:
    """**界面靠它显示"这条带不带附件"**，否则用户没法确认自己传的图有没有生效。"""
    with session_scope() as db:
        task = ApplyTask(status="approved")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    _upload(client, data=PNG, filename="cv.png", task_id=task_id)

    items = client.get("/api/apply/queue").json()
    row = next(item for item in items if item["id"] == task_id)
    assert row["resume_image_path"].endswith(".png")


def test_queue_has_an_empty_attachment_by_default(client: Any) -> None:
    with session_scope() as db:
        db.add(ApplyTask(status="pending"))
        db.commit()

    items = client.get("/api/apply/queue").json()
    assert items[0]["resume_image_path"] == ""


# --------------------------------------------------------------------------- #
# 摘掉附件
# --------------------------------------------------------------------------- #
def _attach(client: Any, task_id: int) -> str:
    return _upload(client, data=PNG, filename="cv.png", task_id=task_id).json()["path"]


def test_detach_clears_the_reference(client: Any) -> None:
    """只能挂不能摘的话，传错一张图就没有退路了。"""
    with session_scope() as db:
        task = ApplyTask(status="approved")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    path = _attach(client, task_id)

    response = client.delete(f"/api/apply/queue/{task_id}/attachment")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert response.json()["previous"] == path

    with session_scope() as db:
        saved = db.get(ApplyTask, task_id)
    assert saved is not None
    assert saved.resume_image_path == ""


def test_detach_does_not_delete_the_file_on_disk(client: Any) -> None:
    """**只断引用、不删盘**：同一张图可能被别的任务引用着。"""
    from app.config import get_settings

    with session_scope() as db:
        task = ApplyTask(status="approved")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    path = _attach(client, task_id)
    target = get_settings().upload_dir / path

    client.delete(f"/api/apply/queue/{task_id}/attachment")

    assert target.is_file(), "摘掉引用不该把文件删了"


def test_detach_when_nothing_was_attached(client: Any) -> None:
    with session_scope() as db:
        task = ApplyTask(status="approved")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    response = client.delete(f"/api/apply/queue/{task_id}/attachment")
    assert response.status_code == 200
    assert response.json()["removed"] is False


def test_detach_missing_task_is_404(client: Any) -> None:
    assert client.delete("/api/apply/queue/999999/attachment").status_code == 404

