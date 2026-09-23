"""表达体检 API（M9）：GET 拿已有报告，POST 生成（会真的去读音频）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..deps import get_session
from ..expression_service import generate, load
from ..models.expression import ExpressionSnapshot
from ..models.session import InterviewSession
from ..schemas.expression import (
    ExpressionReportOut,
    FillerHitOut,
    PauseSpanOut,
    TurnIssueOut,
    WindowMetricOut,
)

router = APIRouter(prefix="/api/sessions", tags=["expression"])


def _to_out(row: ExpressionSnapshot) -> ExpressionReportOut:
    payload = dict(row.report or {})
    longest = payload.get("longest_pause")

    return ExpressionReportOut(
        id=int(row.id or 0),
        session_id=row.session_id,
        recording_id=row.recording_id,
        overall=row.overall,
        summary=row.summary,
        has_audio=row.has_audio,
        frame_ms=row.frame_ms,
        generated_at=row.generated_at,
        speaking_ms=int(payload.get("speaking_ms") or 0),
        chars=int(payload.get("chars") or 0),
        overall_cpm=float(payload.get("overall_cpm") or 0.0),
        filler_total=int(payload.get("filler_total") or 0),
        filler_weighted=float(payload.get("filler_weighted") or 0.0),
        filler_per_kchar=float(payload.get("filler_per_kchar") or 0.0),
        reliable=bool(payload.get("reliable", True)),
        fillers=[FillerHitOut(**item) for item in (payload.get("fillers") or [])],
        pauses=[PauseSpanOut(**item) for item in (payload.get("pauses") or [])],
        longest_pause=PauseSpanOut(**longest) if longest else None,
        windows=[WindowMetricOut(**item) for item in (payload.get("windows") or [])],
        pace_cv=float(payload.get("pace_cv") or 0.0),
        volume_cv=float(payload.get("volume_cv") or 0.0),
        tension=float(payload.get("tension") or 0.0),
        issues=[TurnIssueOut(**item) for item in (payload.get("issues") or [])],
        suggestions=[str(item) for item in (payload.get("suggestions") or [])],
        # 有音频才给地址：前端拿它做"跳到这一句"的回听
        audio_url=(f"/api/sessions/{row.session_id}/audio" if row.has_audio and row.session_id else None),
    )


def _require_session(db: Session, session_id: int) -> InterviewSession:
    session_row = db.get(InterviewSession, session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail=f"面试不存在：{session_id}")
    return session_row


@router.get("/{session_id}/expression", response_model=ExpressionReportOut, summary="已有的表达体检")
def read_expression(session_id: int, db: Session = Depends(get_session)) -> ExpressionReportOut:
    _require_session(db, session_id)
    row = load(db, session_id=session_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="这场面试还没有表达体检。POST 一下就会生成（会读取录音）。",
        )
    return _to_out(row)


@router.post("/{session_id}/expression", response_model=ExpressionReportOut, status_code=201, summary="生成表达体检")
def create_expression(
    session_id: int,
    with_audio: bool = Query(default=True, description="是否读取录音做音量与停顿分析"),
    db: Session = Depends(get_session),
) -> ExpressionReportOut:
    _require_session(db, session_id)
    row = generate(db, session_id=session_id, with_audio=with_audio)
    if row.report.get("chars", 0) == 0:
        raise HTTPException(
            status_code=422,
            detail="这场面试没有你的发言内容，无法做表达体检。",
        )
    return _to_out(row)
