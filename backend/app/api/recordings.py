"""录音条目 API（M4）：新建 / 上传音频 / 列表 / 详情。

"录音"既可以是一段音频，也可以只是一份对话文稿——
Mock 转写模式下我们就是靠文稿跑通整条节奏分析链路的。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete
from sqlmodel import Session, select

from ..clock import utcnow
from ..config import get_settings
from ..deps import get_session
from ..models.pacing import Recording, TranscriptSegment
from ..schemas.pacing import (
    RecordingCreate,
    RecordingDetail,
    RecordingOut,
    TranscriptSegmentOut,
)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


def _to_out(recording: Recording) -> RecordingOut:
    return RecordingOut(
        id=recording.id or 0,
        title=recording.title,
        kind=recording.kind,
        company=recording.company,
        role=recording.role,
        status=recording.status,
        duration_ms=recording.duration_ms,
        error=recording.error,
        created_at=recording.created_at,
        has_audio=bool(recording.path),
        has_text=bool((recording.raw_text or "").strip()),
    )


@router.post("", response_model=RecordingOut, status_code=201, summary="新建录音条目（可只给对话文稿）")
def create_recording(payload: RecordingCreate, db: Session = Depends(get_session)) -> RecordingOut:
    recording = Recording(
        title=payload.title.strip() or "未命名录音",
        kind=payload.kind,
        company=payload.company,
        role=payload.role,
        raw_text=payload.raw_text,
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return _to_out(recording)


@router.post("/upload", response_model=RecordingOut, status_code=201, summary="上传音频文件（可附带文稿）")
async def upload_recording(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    raw_text: str = Form(default=""),
    db: Session = Depends(get_session),
) -> RecordingOut:
    settings = get_settings()
    target_dir = settings.upload_dir / "recordings"
    target_dir.mkdir(parents=True, exist_ok=True)

    # 只取文件名部分，避免 ../ 之类的路径穿越
    safe_name = Path(file.filename or "audio.bin").name
    path = target_dir / f"{utcnow().strftime('%Y%m%d%H%M%S')}_{safe_name}"
    path.write_bytes(await file.read())

    recording = Recording(
        title=title.strip() or safe_name,
        path=str(path),
        raw_text=raw_text,
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return _to_out(recording)


@router.get("", response_model=list[RecordingOut], summary="录音列表")
def list_recordings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[RecordingOut]:
    statement = (
        select(Recording)
        .order_by(Recording.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    return [_to_out(row) for row in db.exec(statement).all()]


@router.get("/{recording_id}", response_model=RecordingDetail, summary="录音详情（含转写段落）")
def get_recording(recording_id: int, db: Session = Depends(get_session)) -> RecordingDetail:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"录音不存在：{recording_id}")

    segments = db.exec(
        select(TranscriptSegment)
        .where(TranscriptSegment.recording_id == recording_id)
        .order_by(TranscriptSegment.idx)  # type: ignore[arg-type]
    ).all()

    base = _to_out(recording)
    return RecordingDetail(
        **base.model_dump(),
        segments=[TranscriptSegmentOut.model_validate(segment) for segment in segments],
    )


@router.delete("/{recording_id}", status_code=204, summary="删除录音（连带转写段落）")
def delete_recording(recording_id: int, db: Session = Depends(get_session)) -> None:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"录音不存在：{recording_id}")

    # 先删子表段落再删录音（同一个事务里顺序确定）
    db.exec(delete(TranscriptSegment).where(TranscriptSegment.recording_id == recording_id))
    db.delete(recording)
    db.commit()
