"""M8 的简历智能与话术 API：故事卡、话术草稿、按匹配分的投递分段统计。

路由不挂在某个前缀下，是因为它跨三个资源（/api/stories、/api/answers、
/api/applications/match-bands），共用同一套"从简历长出来"的逻辑。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..analysis.answer_bank import SELF_INTRO_LENGTHS, SENSITIVE_QUESTIONS, char_budget, spoken_seconds
from ..analysis.match_stats import BandInput, build_bands
from ..answer_service import compose_questions, compose_self_intro, compose_sensitive, list_drafts
from ..clock import utcnow
from ..deps import get_llm, get_session
from ..models.apply import Application
from ..models.core import Resume
from ..models.story import STORY_TAGS, AnswerDraft, StoryCard
from ..providers.llm.base import LLMClient, LLMError
from ..schemas.resume_intel import (
    AnswerDraftOut,
    BandStatOut,
    GenerateStoriesIn,
    MatchBandReportOut,
    QuestionsIn,
    SelfIntroIn,
    SelfIntroOptionOut,
    SensitiveIn,
    SensitiveQuestionOut,
    StoryCardIn,
    StoryCardOut,
    StoryListOut,
)
from ..story_service import coverage, generate_stories, list_cards, missing_tags

router = APIRouter(tags=["resume-intel"])


def _story_out(card: StoryCard) -> StoryCardOut:
    payload = {
        "id": int(card.id or 0),
        "title": card.title,
        "situation": card.situation,
        "task": card.task,
        "action": card.action,
        "result": card.result,
        "metrics": card.metrics,
        "tags": list(card.tags or []),
        "topics": list(card.topics or []),
        "starred": card.starred,
        "source_resume_id": card.source_resume_id,
        "used_count": card.used_count,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }
    return StoryCardOut(**payload)


def _draft_out(draft: AnswerDraft) -> AnswerDraftOut:
    return AnswerDraftOut(
        id=int(draft.id or 0),
        kind=draft.kind,
        title=draft.title,
        resume_id=draft.resume_id,
        content=draft.content,
        target_seconds=draft.target_seconds,
        basis={**dict(draft.basis or {}), "estimated_seconds": spoken_seconds(draft.content)},
        created_at=draft.created_at,
    )


# --------------------------------------------------------------------------- #
# 故事卡
# --------------------------------------------------------------------------- #
@router.get("/api/stories", response_model=StoryListOut, summary="故事卡列表 + 题型覆盖情况")
def read_stories(
    resume_id: int | None = Query(default=None),
    db: Session = Depends(get_session),
) -> StoryListOut:
    cards = list_cards(db, resume_id=resume_id)
    return StoryListOut(
        items=[_story_out(card) for card in cards],
        coverage=coverage(db),
        missing_tags=missing_tags(db),
        tag_labels=dict(STORY_TAGS),
    )


@router.post("/api/stories/generate", response_model=list[StoryCardOut], status_code=201, summary="从简历里抽取故事卡")
async def create_stories(
    payload: GenerateStoriesIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> list[StoryCardOut]:
    try:
        cards = await generate_stories(
            db,
            llm=llm,
            resume_id=payload.resume_id,
            count=payload.count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"生成故事卡失败：{exc}") from exc

    return [_story_out(card) for card in cards]


@router.post("/api/stories", response_model=StoryCardOut, status_code=201, summary="手写一张故事卡")
def create_story(payload: StoryCardIn, db: Session = Depends(get_session)) -> StoryCardOut:
    card = StoryCard(**payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return _story_out(card)


@router.put("/api/stories/{card_id}", response_model=StoryCardOut, summary="修改故事卡")
def update_story(card_id: int, payload: StoryCardIn, db: Session = Depends(get_session)) -> StoryCardOut:
    card = db.get(StoryCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"故事卡不存在：{card_id}")

    for field_name, value in payload.model_dump().items():
        setattr(card, field_name, value)
    card.updated_at = utcnow()
    db.add(card)
    db.commit()
    db.refresh(card)
    return _story_out(card)


@router.delete("/api/stories/{card_id}", status_code=204, summary="删除故事卡")
def delete_story(card_id: int, db: Session = Depends(get_session)) -> None:
    card = db.get(StoryCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"故事卡不存在：{card_id}")
    db.delete(card)
    db.commit()


# --------------------------------------------------------------------------- #
# 话术
# --------------------------------------------------------------------------- #
@router.get("/api/answers/meta", response_model=dict, summary="敏感题清单与自我介绍时长选项")
def read_answer_meta() -> dict:
    return {
        "sensitive": [
            SensitiveQuestionOut(key=key, label=label, question=question, strategy=strategy).model_dump()
            for key, (label, question, strategy) in SENSITIVE_QUESTIONS.items()
        ],
        "self_intro_options": [
            SelfIntroOptionOut(seconds=seconds, label=f"{seconds} 秒", target_chars=char_budget(seconds)).model_dump()
            for seconds in SELF_INTRO_LENGTHS
        ],
        "tag_labels": dict(STORY_TAGS),
    }


@router.get("/api/answers", response_model=list[AnswerDraftOut], summary="话术草稿（含历史版本）")
def read_answers(
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[AnswerDraftOut]:
    return [_draft_out(draft) for draft in list_drafts(db, kind=kind, limit=limit)]


@router.post("/api/answers/self-intro", response_model=AnswerDraftOut, status_code=201, summary="生成自我介绍")
async def create_self_intro(
    payload: SelfIntroIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> AnswerDraftOut:
    try:
        draft = await compose_self_intro(
            db,
            llm=llm,
            resume_id=payload.resume_id,
            seconds=payload.seconds,
            jd_text=payload.jd_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"生成自我介绍失败：{exc}") from exc
    return _draft_out(draft)


@router.post("/api/answers/sensitive", response_model=AnswerDraftOut, status_code=201, summary="生成敏感题回答")
async def create_sensitive(
    payload: SensitiveIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> AnswerDraftOut:
    try:
        draft = await compose_sensitive(
            db,
            llm=llm,
            kind=payload.kind,
            resume_id=payload.resume_id,
            jd_text=payload.jd_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"生成敏感题回答失败：{exc}") from exc
    return _draft_out(draft)


@router.post("/api/answers/questions", response_model=AnswerDraftOut, status_code=201, summary="生成反问清单")
async def create_questions(
    payload: QuestionsIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> AnswerDraftOut:
    if not payload.jd_text.strip():
        raise HTTPException(status_code=422, detail="反问清单需要一段 JD 才有针对性")

    try:
        draft = await compose_questions(
            db,
            llm=llm,
            jd_text=payload.jd_text,
            company=payload.company,
            resume_id=payload.resume_id,
            count=payload.count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"生成反问清单失败：{exc}") from exc
    return _draft_out(draft)


@router.delete("/api/answers/{draft_id}", status_code=204, summary="删除话术草稿")
def delete_answer(draft_id: int, db: Session = Depends(get_session)) -> None:
    draft = db.get(AnswerDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"话术不存在：{draft_id}")
    db.delete(draft)
    db.commit()


# --------------------------------------------------------------------------- #
# 匹配分段统计（与 M6 的投递数据打通）
# --------------------------------------------------------------------------- #
@router.get("/api/applications/match-bands", response_model=MatchBandReportOut, summary="按匹配分区间看回复率")
def read_match_bands(
    resume_id: int | None = Query(default=None),
    db: Session = Depends(get_session),
) -> MatchBandReportOut:
    if resume_id is not None:
        resume = db.get(Resume, resume_id)
        if resume is None:
            raise HTTPException(status_code=404, detail=f"简历不存在：{resume_id}")
        resume_text = resume.content_text
    else:
        latest = db.exec(select(Resume).order_by(Resume.id.desc())).first()  # type: ignore[attr-defined]
        resume_text = latest.content_text if latest is not None else ""

    applications = list(db.exec(select(Application)).all())
    report = build_bands(
        [
            BandInput(jd_text=item.jd_text, status=item.status, company=item.company)
            for item in applications
        ],
        resume_text,
        resume_id=resume_id,
    )

    return MatchBandReportOut(
        resume_id=report.resume_id,
        total_records=report.total_records,
        matched_records=report.matched_records,
        bands=[BandStatOut(**asdict(stat)) for stat in report.bands],
        recommendation=report.recommendation,
        confidence_note=report.confidence_note,
    )
