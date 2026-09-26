"""录音条目 API（M4）：新建 / 上传音频 / 上传文稿 / 列表 / 详情。

"录音"既可以是一段音频，也可以只是一份对话文稿——
Mock 转写模式下我们就是靠文稿跑通整条节奏分析链路的。

**关于音频**：音频只做保存，**不会识别成文字**（`ASR_PROVIDER=mock` 时）。
想学面试官节奏请上传对话文稿（.txt/.md）或直接粘贴文本——
页面上也是这么引导的，不让人误以为传了音频就能自动转写。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete
from sqlmodel import Session, select

from ..analysis.glossary import correct_transcript
from ..analysis.pacing import parse_transcript
from ..analysis.transcript_doc import (
    SUPPORTED_SUFFIXES,
    TranscriptDocError,
    check_document,
    decode_document,
)
from ..clock import utcnow
from ..config import get_settings
from ..deps import get_session
from ..models.pacing import InterviewerProfile, PacingSample, Recording, TranscriptSegment
from ..schemas.pacing import (
    DocCheckOut,
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


def _write_segments(db: Session, recording: Recording, text: str) -> int:
    """把文稿解析成段落落库，返回写入条数。

    粘贴文稿与上传文稿都应该走这里。否则粘贴出来的对话要等"生成画像"才有段落，
    用户点「看段落」看到的是一片空白——会以为功能坏了。
    """
    drafts = parse_transcript(text)
    for draft in drafts:
        db.add(
            TranscriptSegment(
                recording_id=recording.id or 0,
                idx=draft.idx,
                speaker=draft.speaker,
                # 与实时链路同一套术语纠错：录音转写的误识别和实时的一样多
                text=correct_transcript(draft.text)[0],
                start_ms=draft.start_ms,
                end_ms=draft.end_ms,
            )
        )
    return len(drafts)


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

    # 粘贴的文稿立刻解析成段落（与上传文稿一致），「看段落」才有东西可看
    if payload.raw_text.strip():
        _write_segments(db, recording, payload.raw_text)
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


@router.post("/document", response_model=RecordingOut, status_code=201, summary="上传对话文稿文件（.txt/.md）")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    company: str = Form(default=""),
    role: str = Form(default=""),
    db: Session = Depends(get_session),
) -> RecordingOut:
    """把一份对话文稿文件读成文本并存成录音条目。

    与 `/upload`（音频）分开，是因为两者的失败模式完全不同：
    音频是"存下来但识别不了"，文稿是"编码可能读错、格式可能切不出问答"。
    混在一个端点里，错误提示就只能说些模棱两可的话。
    """
    safe_name = Path(file.filename or "transcript.txt").name
    suffix = Path(safe_name).suffix.lower()
    if suffix and suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件类型 `{suffix}`。"
                f"请上传纯文本文稿（{'、'.join(SUPPORTED_SUFFIXES)}），"
                "或把内容直接粘贴进文本框。"
            ),
        )

    data = await file.read()
    try:
        text, encoding = decode_document(data)
    except TranscriptDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not text.strip():
        raise HTTPException(status_code=400, detail="文件里没有内容")

    recording = Recording(
        title=title.strip() or Path(safe_name).stem or "未命名文稿",
        kind="real_interview",
        company=company,
        role=role,
        raw_text=text,
    )
    db.add(recording)
    db.commit()
    db.refresh(recording)

    # 文稿是**确定有文本**的，所以当场就把段落落库——
    # 不然用户上传完只看到一条"待分析"，还得再点一次「生成画像」才知道能不能用。
    # 这也让「看段落」在生成画像前就有内容可核对。
    if _write_segments(db, recording, text):
        recording.status = "uploaded"
    db.add(recording)
    db.commit()
    db.refresh(recording)
    return _to_out(recording)


@router.post("/inspect", response_model=DocCheckOut, summary="体检一份对话文稿（不落库）")
async def inspect_document(file: UploadFile = File(...)) -> DocCheckOut:
    """上传前先解析一遍，告诉用户"能切出多少问答、缺什么"。

    这一步存在的理由：说话人标记（`[面试官]` / `[我]`）缺了，
    节奏分析会**静默**丢掉那些行，最后只在生成画像时报一句
    "没有识别出任何面试问题"——那时用户已经不知道是哪一步的问题了。
    """
    safe_name = Path(file.filename or "transcript.txt").name
    data = await file.read()
    try:
        text, encoding = decode_document(data)
    except TranscriptDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    check = check_document(text, encoding=encoding)
    parsed = parse_transcript(text)
    return DocCheckOut(
        encoding=check.encoding,
        filename=safe_name,
        total_lines=check.total_lines,
        segments=check.segments,
        labeled=check.labeled,
        interviewer=check.interviewer,
        candidate=check.candidate,
        timestamped=check.timestamped,
        looks_like_questions=check.looks_like_questions,
        inferred_roles=check.inferred_roles,
        role_note=check.role_note,
        usable=check.usable,
        summary=check.summary(),
        warnings=check.warnings,
        preview=[
            TranscriptSegmentOut(
                idx=draft.idx,
                speaker=draft.speaker,
                text=draft.text,
                start_ms=draft.start_ms,
                end_ms=draft.end_ms,
                source_label=draft.source_label,
            )
            for draft in parsed[:8]
        ],
    )


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


@router.delete(
    "/{recording_id}",
    status_code=204,
    summary="删除录音（连带转写段落；依赖它的画像也会一起删）",
)
def delete_recording(recording_id: int, db: Session = Depends(get_session)) -> None:
    """删掉一段对话，连同所有指向它的东西。

    `recording` 被两张表引用：`transcript_segment` 与 `pacing_sample`。
    只清前者时，一旦这段对话生成过画像，删它就会撞外键
    （`FOREIGN KEY constraint failed` → 500）。

    依赖它的画像一并删除，而不是留着：画像的 `recording_ids` 会指向已删的录音，
    用户看到的将是一份"说不出从哪学来的"画像——那种数据留着比删掉更糟。
    只删"引用了这段对话"的画像，不碰其它画像。
    """
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail=f"录音不存在：{recording_id}")

    # 找出依赖这段对话的画像（recording_ids 是 JSON 数组，只能在 Python 侧过滤）
    dependent: list[InterviewerProfile] = []
    for profile in db.exec(select(InterviewerProfile)).all():
        if recording_id in (profile.recording_ids or []):
            dependent.append(profile)

    # 顺序：先删子表（pacing_sample / transcript_segment），再删画像，最后删录音。
    for profile in dependent:
        db.exec(delete(PacingSample).where(PacingSample.profile_id == profile.id))
        db.delete(profile)
    # 兜底：即使画像已不存在，也可能有孤儿样本指向这段录音
    db.exec(delete(PacingSample).where(PacingSample.recording_id == recording_id))
    db.exec(delete(TranscriptSegment).where(TranscriptSegment.recording_id == recording_id))
    db.delete(recording)
    db.commit()
