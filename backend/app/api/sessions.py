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

from ..analysis.interviewer_styles import resolve_style, style_prompt_block
from ..analysis.locale import locale_prompt_block, resolve_locale
from ..analysis.pacing import build_policy, policy_prompt_block, policy_summary
from ..analysis.panels import panel_prompt_block, parse_panel
from ..analysis.pressure import normalize_pressure, pressure_prompt_block
from ..analysis.question_bank import BankQuestion, bank_prompt_block
from ..clock import utcnow
from ..config import get_settings
from ..deletion import DeletionError, delete_session
from ..deps import get_llm, get_session
from ..models.core import JobTarget, Resume
from ..models.pacing import InterviewerProfile
from ..models.session import InterviewSession, SessionTurn
from ..pacing_service import load_policy_for_session, profile_samples
from ..providers.llm.base import LLMClient, LLMError
from ..realtime.interviewer import Interviewer
from ..repositories import count_turns, ordered_turns
from ..story_service import prompt_block_for
from ..vision_service import pick_questions
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


def _bank_block_from_session(session_row: InterviewSession) -> str:
    """从会话的 plan 里还原题库清单。

    每轮都要把清单带上，模型才知道按序问到了哪——所以它存在 plan 里，
    而不是只活在创建会话时的那个局部变量里。
    """
    stored = (session_row.plan or {}).get("questions") or []
    if not stored:
        return ""
    return bank_prompt_block(
        [
            BankQuestion(
                id=int(item.get("id") or 0),
                text=str(item.get("text") or ""),
                points=[str(point) for point in (item.get("points") or [])],
                category=str(item.get("category") or ""),
            )
            for item in stored
        ]
    )


def _target_label(target: JobTarget | None) -> str:
    if target is None:
        return ""
    return " · ".join(p for p in (target.company, target.title) if p)


def _compose_policy_block(
    *,
    style: Any,
    panel: Any,
    panel_round: int,
    pressure: Any,
    policy: Any,
    locale: str,
) -> str:
    """把"这场面试怎么问"的几类指令合成一个块。

    顺序有讲究：越靠近末尾的指令越容易被模型遵守，
    所以风格与角色放前面、画像节奏其次、"用什么语言"放最后。
    """
    blocks: list[str] = []
    if style is not None:
        blocks.append(style_prompt_block(style, locale=locale))
    if panel is not None:
        blocks.append(panel_prompt_block(panel, panel_round, locale=locale))
    if pressure is not None and pressure.enabled:
        blocks.append(pressure_prompt_block(pressure, locale=locale))
    if policy is not None:
        blocks.append(policy_prompt_block(policy))
    if locale != "zh":
        blocks.append(locale_prompt_block(locale))
    return "\n\n".join(block for block in blocks if block.strip())


def _session_options(session_row: InterviewSession) -> tuple[Any, Any, Any, str]:
    """从 session.plan 里还原这场面试的配置（压力 / 连环面 / 风格 / 语言）。"""
    plan = session_row.plan or {}
    pressure = normalize_pressure(plan.get("pressure"))
    panel = parse_panel(plan.get("panel"))
    style_key = plan.get("style_key")
    style = resolve_style(style_key) if style_key else None
    locale_key = str(plan.get("locale") or "zh")
    return pressure, panel, style, locale_key


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
    provider = getattr(llm, "name", "")

    # M11：压力模式 / 面试官风格预设 / 连环面 / 语言
    pressure = normalize_pressure(payload.pressure)
    locale_key = resolve_locale(payload.locale).key
    panel = parse_panel(payload.panel)
    style_preset = resolve_style(payload.style_key) if payload.style_key else None
    if panel is not None:
        # 连环面时每轮的角色由 panel 决定，单独的 style_key 让位
        style_preset = panel.style_for(0)
    style = payload.style or (style_preset.label if style_preset else DEFAULT_STYLE)

    # M4：选了面试官画像 → 这场面试按那个节奏来
    profile: InterviewerProfile | None = None
    if payload.interviewer_profile_id is not None:
        profile = db.get(InterviewerProfile, payload.interviewer_profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"面试官画像不存在：{payload.interviewer_profile_id}")

    # M13：按题源占比组题。抽出来就固定进 plan——面试过程中不再变，
    # 否则题目会中途换掉，事后也说不清到底问了哪些。
    bank_questions: list[BankQuestion] = []
    if payload.question_mix:
        bank_questions = pick_questions(
            db,
            mix=payload.question_mix,
            role=payload.question_role,
            category=payload.question_category,
            max_difficulty=payload.question_max_difficulty,
            count=payload.question_count or max_questions,
        )
        # 抽不到题时不报错：退回自由出题，而不是让一场面试开不起来
    bank_block = bank_prompt_block(bank_questions)

    session_plan: dict[str, Any] = {
        "max_questions": max_questions,
        "style": style,
        "locale": locale_key,
        "pressure": pressure.to_dict(),
    }
    if bank_questions:
        # 要点也一起存：报告里要拿它对照「哪些点答到了」
        session_plan["questions"] = [
            {"id": item.id, "text": item.text, "category": item.category, "points": item.points}
            for item in bank_questions
        ]
        session_plan["question_mix"] = payload.question_mix
        # 实际抽到的来源分布——请求的比例可能因为某来源题不够而被调整，
        # 记录实际值才能解释「为什么这套题的配比和我设的不一样」
        actual: dict[str, int] = {}
        for item in bank_questions:
            actual[item.origin] = actual.get(item.origin, 0) + 1
        session_plan["question_sources"] = actual
        session_plan["question_filters"] = {
            "role": payload.question_role,
            "category": payload.question_category,
            "max_difficulty": payload.question_max_difficulty,
        }
    if style_preset is not None:
        session_plan["style_key"] = style_preset.key
        session_plan["style_label"] = style_preset.label
        # 语音链路按角色选音色（多角色连环面时每轮都不同）
        session_plan["interviewer_voice"] = style_preset.voice
    if panel is not None:
        session_plan["panel"] = panel.to_dict()
        session_plan["panel_current"] = panel.current(0)
    policy = (
        build_policy(
            profile.params,
            default_silence_ms=settings.speech_silence_ms,
            profile_id=profile.id,
            profile_name=profile.name,
            # 真实原题在样本表里，必须一起读——否则模型只知道"爱问技术题"，
            # 不知道这个人具体关心什么
            samples=profile_samples(db, profile.id or 0),
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
    # M11：开场用的行为指令（风格 → 角色 → 压力 → 画像 → 语言）
    opening_policy_block = _compose_policy_block(
        style=style_preset,
        panel=panel,
        panel_round=0,
        pressure=pressure,
        policy=policy,
        locale=locale_key,
    )
    try:
        plan, result = await interviewer.next_question(
            turns=[],
            target=target,
            resume=resume,
            max_questions=max_questions,
            style=style,
            policy_block=opening_policy_block,
            bank_block=bank_block,
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
        meta={**dict(plan.raw or {}), "interviewer": panel.current(0)} if panel is not None else plan.raw,
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
    # M8：按"上一个问题 + 这次回答"挑出最相关的故事卡，让追问落在你真做过的经历上
    last_question = next((turn.text for turn in reversed(turns) if turn.speaker == "interviewer"), "")
    story_block = prompt_block_for(db, question=f"{last_question}\n{payload.text}", limit=2) if last_question else ""

    # M11：这一轮该由谁问（连环面会换人，音色也跟着换）
    pressure, panel, style_preset, locale_key = _session_options(session_row)
    round_index = answer_count
    if panel is not None:
        style_preset = panel.style_for(round_index)
    question_policy_block = _compose_policy_block(
        style=style_preset,
        panel=panel,
        panel_round=round_index,
        pressure=pressure,
        policy=policy,
        locale=locale_key,
    )
    current_interviewer = panel.current(round_index) if panel is not None else {}

    try:
        plan, result = await interviewer.next_question(
            turns=turns,
            target=_load_target(db, session_row),
            resume=_load_resume(db, session_row),
            max_questions=max_questions,
            style=style,
            closing=closing,
            policy_block=question_policy_block,
            story_block=story_block,
            bank_block=_bank_block_from_session(session_row),
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
        meta={**dict(plan.raw or {}), "interviewer": current_interviewer} if current_interviewer else plan.raw,
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
        # M12：薪资谈判复用 interview_session，但它不是面试，不该出现在历史面试里
        .where(InterviewSession.mode != "negotiation")
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


@router.delete(
    "/{session_id}",
    status_code=204,
    summary="删除一场面试（连带回合、作答、报告与录音文件）",
)
def delete_session_route(session_id: int, db: Session = Depends(get_session)) -> None:
    try:
        delete_session(db, session_id)
    except DeletionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
