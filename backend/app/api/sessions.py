"""会话 API：创建面试、提交回答、结束、查询。

M0 走文字模式；M1 的 realtime 模式会复用这里完全相同的落库与编排逻辑，
只在传输层换成 WebSocket —— 所以本模块不做任何音频相关假设。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlmodel import Session, select

from ..analysis.pacing import build_policy, policy_prompt_block, policy_summary
from ..clock import utcnow
from ..config import get_settings
from ..deps import get_llm, get_session
from ..models.core import JobTarget, Resume
from ..models.pacing import InterviewerProfile
from ..models.session import InterviewSession, SessionTurn
from ..pacing_service import load_policy_for_session
from ..providers.llm.base import LLMClient, LLMError
from ..realtime.interviewer import Interviewer
from ..repositories import count_turns, ordered_turns
from ..schemas import (
    AnswerIn,
    AnswerOut,
    SessionCreate,
    SessionDetail,
    SessionListItem,
    SessionOut,
    TurnOut,
)
from ..services import log_llm_error, log_llm_result, mark_finished, turn_audio_url

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

DEFAULT_STYLE = "标准技术面"
STATUS_LIVE = "live"
STATUS_FINISHED = "finished"


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _turns(db: Session, session_id: int) -> list[SessionTurn]:
    return ordered_turns(db, session_id)


def _counts(turns: list[SessionTurn]) -> tuple[int, int]:
    return count_turns(turns)


def _target_label(target: JobTarget | None) -> str:
    if target is None:
        return ""
    return " · ".join(p for p in (target.company, target.title) if p)


def _load_target(db: Session, session_row: InterviewSession) -> JobTarget | None:
    return db.get(JobTarget, session_row.target_id) if session_row.target_id else None


def _load_resume(db: Session, session_row: InterviewSession) -> Resume | None:
    return db.get(Resume, session_row.resume_id) if session_row.resume_id else None


def _elapsed_ms(session_row: InterviewSession) -> int:
    started = session_row.started_at or session_row.created_at
    return max(0, int((utcnow() - started).total_seconds() * 1000))


def _audio_url(session_id: int, audio_path: str | None) -> str | None:
    return turn_audio_url(session_id, audio_path)


def _turn_out(session_id: int, turn: SessionTurn) -> TurnOut:
    item = TurnOut.model_validate(turn)
    item.audio_url = _audio_url(session_id, turn.audio_path)
    return item


def _detail(session_row: InterviewSession, turns: list[SessionTurn], db: Session) -> SessionDetail:
    questions, answers = _counts(turns)
    base = SessionOut.model_validate(session_row).model_dump()
    session_id = session_row.id or 0
    return SessionDetail(
        **base,
        target_label=_target_label(_load_target(db, session_row)),
        question_count=questions,
        answer_count=answers,
        turns=[_turn_out(session_id, turn) for turn in turns],
    )


def _mark_finished(session_row: InterviewSession, db: Session) -> None:
    mark_finished(db, session_row)


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.post("", response_model=SessionDetail, status_code=201, summary="创建一场面试并拿到开场问题")
async def create_session(
    payload: SessionCreate,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> SessionDetail:
    settings = get_settings()

    target: JobTarget | None = None
    if payload.target_id is not None:
        target = db.get(JobTarget, payload.target_id)
        if target is None:
            raise HTTPException(status_code=404, detail=f"岗位不存在：{payload.target_id}")

    resume: Resume | None = None
    if payload.resume_id is not None:
        resume = db.get(Resume, payload.resume_id)
        if resume is None:
            raise HTTPException(status_code=404, detail=f"简历不存在：{payload.resume_id}")

    max_questions = payload.max_questions or settings.default_max_questions
    style = payload.style or DEFAULT_STYLE
    provider = getattr(llm, "name", "")

    # M4：选了面试官画像 → 这场面试按那个节奏来
    profile: InterviewerProfile | None = None
    if payload.interviewer_profile_id is not None:
        profile = db.get(InterviewerProfile, payload.interviewer_profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"面试官画像不存在：{payload.interviewer_profile_id}")

    session_plan: dict[str, Any] = {"max_questions": max_questions, "style": style}
    policy = (
        build_policy(
            profile.params,
            default_silence_ms=settings.speech_silence_ms,
            profile_id=profile.id,
            profile_name=profile.name,
        )
        if profile is not None
        else None
    )
    if policy is not None:
        # 把"这场会用到的节奏"落进 plan：前端能直接展示，测试也能断言画像真的生效了
        session_plan["pacing"] = policy_summary(policy)

    session_row = InterviewSession(
        mode=payload.mode,
        status=STATUS_LIVE,
        provider=provider,
        target_id=payload.target_id,
        resume_id=payload.resume_id,
        interviewer_profile_id=payload.interviewer_profile_id,
        plan=session_plan,
        started_at=utcnow(),
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    session_id = session_row.id or 0

    interviewer = Interviewer(llm)
    try:
        plan, result = await interviewer.next_question(
            turns=[],
            target=target,
            resume=resume,
            max_questions=max_questions,
            style=style,
            policy_block=policy_prompt_block(policy) if policy is not None else "",
        )
    except LLMError as exc:
        log_llm_error(db, task="interview_turn", provider=provider, error=str(exc), session_id=session_id)
        session_row.status = "failed"
        db.add(session_row)
        db.commit()
        raise HTTPException(status_code=502, detail=f"生成开场问题失败：{exc}") from exc

    log_llm_result(db, result, session_id=session_id)

    opening = SessionTurn(
        session_id=session_id,
        idx=0,
        speaker="interviewer",
        text=plan.question,
        kind=plan.kind or "opening",
        topic=plan.topic,
        meta=plan.raw,
        start_ms=0,
        end_ms=0,
    )
    db.add(opening)
    db.commit()
    db.refresh(opening)

    return _detail(session_row, _turns(db, session_id), db)


@router.post("/{session_id}/answer", response_model=AnswerOut, summary="提交本轮回答，返回下一个问题")
async def submit_answer(
    session_id: int,
    payload: AnswerIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> AnswerOut:
    session_row = db.get(InterviewSession, session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    if session_row.status != STATUS_LIVE:
        raise HTTPException(
            status_code=409,
            detail=f"会话当前状态为 {session_row.status}，无法继续作答（请新建一场，或直接查看报告）",
        )

    settings = get_settings()
    turns = _turns(db, session_id)
    max_questions = int(session_row.plan.get("max_questions") or settings.default_max_questions)
    style = str(session_row.plan.get("style") or DEFAULT_STYLE)
    provider = getattr(llm, "name", "")

    elapsed = _elapsed_ms(session_row)
    previous_end = turns[-1].end_ms if turns and turns[-1].end_ms is not None else 0
    answer_turn = SessionTurn(
        session_id=session_id,
        idx=len(turns),
        speaker="candidate",
        text=payload.text.strip(),
        kind="answer",
        start_ms=previous_end,
        end_ms=elapsed,
    )
    db.add(answer_turn)
    db.commit()
    db.refresh(answer_turn)
    turns.append(answer_turn)

    _, answer_count = _counts(turns)
    closing = answer_count >= max_questions

    interviewer = Interviewer(llm)
    policy = load_policy_for_session(db, session_row, default_silence_ms=settings.speech_silence_ms)
    try:
        plan, result = await interviewer.next_question(
            turns=turns,
            target=_load_target(db, session_row),
            resume=_load_resume(db, session_row),
            max_questions=max_questions,
            style=style,
            closing=closing,
            policy_block=policy_prompt_block(policy) if policy is not None else "",
        )
    except LLMError as exc:
        log_llm_error(db, task="interview_turn", provider=provider, error=str(exc), session_id=session_id)
        raise HTTPException(status_code=502, detail=f"生成下一个问题失败：{exc}") from exc

    log_llm_result(db, result, session_id=session_id)

    question_turn = SessionTurn(
        session_id=session_id,
        idx=len(turns),
        speaker="interviewer",
        text=plan.question,
        kind="closing" if closing else (plan.kind or "new_topic"),
        topic=plan.topic,
        is_followup=plan.is_followup and not closing,
        meta=plan.raw,
        start_ms=elapsed,
        end_ms=elapsed,
    )
    db.add(question_turn)
    db.commit()
    db.refresh(question_turn)

    if closing:
        _mark_finished(session_row, db)

    return AnswerOut(
        session=_detail(session_row, _turns(db, session_id), db),
        question=TurnOut.model_validate(question_turn),
        finished=closing,
        message="面试已结束。评分报告在 M3 提供。" if closing else "",
    )


@router.post("/{session_id}/finish", response_model=SessionDetail, summary="提前结束一场面试")
def finish_session(session_id: int, db: Session = Depends(get_session)) -> SessionDetail:
    session_row = db.get(InterviewSession, session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    _mark_finished(session_row, db)
    return _detail(session_row, _turns(db, session_id), db)


@router.get("", response_model=list[SessionListItem], summary="历史会话")
def list_sessions(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[SessionListItem]:
    statement = (
        select(InterviewSession)
        .order_by(InterviewSession.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    rows = list(db.exec(statement).all())
    session_ids = [r.id for r in rows if r.id is not None]

    turn_rows: list[tuple[int, int]] = []
    if session_ids:
        counting = (
            select(SessionTurn.session_id, func.count(SessionTurn.id))
            .where(SessionTurn.session_id.in_(session_ids))  # type: ignore[attr-defined]
            .group_by(SessionTurn.session_id)
        )
        turn_rows = [(int(sid), int(count)) for sid, count in db.exec(counting).all()]

    # 会话数量级很小，这里按会话逐个统计提问/作答，逻辑直观优先
    per_session: dict[int, tuple[int, int]] = {}
    for sid in session_ids:
        turns = _turns(db, sid)
        per_session[sid] = _counts(turns)

    items: list[SessionListItem] = []
    for row in rows:
        questions, answers = per_session.get(row.id or 0, (0, 0))
        base = SessionOut.model_validate(row).model_dump()
        items.append(
            SessionListItem(
                **base,
                target_label=_target_label(_load_target(db, row)),
                question_count=questions,
                answer_count=answers,
            )
        )
    return items


@router.get("/{session_id}", response_model=SessionDetail, summary="会话详情（含全部回合）")
def get_session_detail(session_id: int, db: Session = Depends(get_session)) -> SessionDetail:
    session_row = db.get(InterviewSession, session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return _detail(session_row, _turns(db, session_id), db)


@router.get("/{session_id}/audio", summary="整场录音（WAV）")
def download_session_audio(session_id: int, db: Session = Depends(get_session)) -> FileResponse:
    if db.get(InterviewSession, session_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")

    path = get_settings().audio_dir / str(session_id) / "full.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="该会话还没有录音（文字模式不产生音频）")
    return FileResponse(path, media_type="audio/wav", filename=f"session_{session_id}.wav")


@router.get("/{session_id}/audio/turns/{index}", summary="某一回合的录音切片（WAV）")
def download_turn_audio(session_id: int, index: int, db: Session = Depends(get_session)) -> FileResponse:
    if db.get(InterviewSession, session_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")

    path = get_settings().audio_dir / str(session_id) / f"turn_{index:03d}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"该回合没有音频切片：{index}")
    return FileResponse(path, media_type="audio/wav", filename=f"session_{session_id}_turn_{index:03d}.wav")
