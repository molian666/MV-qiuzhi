"""故事卡服务（M8）：生成 → 入库 → 在模拟面试里自动引用。

「自动引用」的落点在 `prompt_block_for`：面试官在问下一个问题前，
会按问题类型挑出最合适的故事卡，拼成 prompt 里的一段事实底稿。
这样"讲一个你最自豪的项目"得到的追问，会追在**你真做过的那件事**上，
而不是模型编出来的项目上。
"""

from __future__ import annotations

from typing import Sequence

from sqlmodel import Session, select

from .analysis.story_card import (
    StoryRef,
    build_story_messages,
    missing_story_tags,
    normalize_stories,
    select_stories,
    story_card_prompt_block,
    story_coverage,
)
from .clock import utcnow
from .models.core import Resume
from .models.story import StoryCard
from .providers.llm.base import LLMClient, LLMError
from .services import log_llm_error, log_llm_result


def refs_from_cards(cards: Sequence[StoryCard]) -> list[StoryRef]:
    return [
        StoryRef(
            id=card.id,
            title=card.title,
            tags=list(card.tags or []),
            topics=list(card.topics or []),
            situation=card.situation,
            task=card.task,
            action=card.action,
            result=card.result,
            metrics=card.metrics,
            starred=card.starred,
            used_count=card.used_count,
        )
        for card in cards
    ]


def list_cards(
    db: Session,
    *,
    resume_id: int | None = None,
    starred_only: bool = False,
) -> list[StoryCard]:
    statement = select(StoryCard)
    if resume_id is not None:
        statement = statement.where(StoryCard.source_resume_id == resume_id)
    if starred_only:
        statement = statement.where(StoryCard.starred.is_(True))  # type: ignore[union-attr]
    # 主推的排前面，其余按创建顺序
    return list(db.exec(statement.order_by(StoryCard.starred.desc(), StoryCard.id)).all())  # type: ignore[arg-type]


def resume_text_for(db: Session, resume_id: int | None) -> str:
    """取指定简历；没指定就用最新的一份。"""
    if resume_id is not None:
        resume = db.get(Resume, resume_id)
        if resume is None:
            raise ValueError(f"简历不存在：{resume_id}")
        return resume.content_text

    latest = db.exec(select(Resume).order_by(Resume.id.desc())).first()  # type: ignore[attr-defined]
    return latest.content_text if latest is not None else ""


def save_cards(db: Session, stories: Sequence[dict], *, resume_id: int | None = None) -> list[StoryCard]:
    """入库，按标题去重（同一段经历重复生成不该变成两张卡）。"""
    existing = {card.title.strip().lower() for card in list_cards(db)}
    saved: list[StoryCard] = []
    for story in stories:
        title = str(story.get("title") or "").strip()
        if not title or title.lower() in existing:
            continue
        card = StoryCard(source_resume_id=resume_id, **story)
        db.add(card)
        existing.add(title.lower())
        saved.append(card)

    db.commit()
    for card in saved:
        db.refresh(card)
    return saved


async def generate_stories(
    db: Session,
    *,
    llm: LLMClient,
    resume_id: int | None = None,
    count: int = 4,
) -> list[StoryCard]:
    """从简历里抽故事卡。简历里已有的标题会传给模型，避免重复生成。"""
    resume_text = resume_text_for(db, resume_id)
    if not resume_text.strip():
        return []

    known_titles = [card.title for card in list_cards(db)]
    messages = build_story_messages(resume_text=resume_text, existing_titles=known_titles, count=count)

    try:
        result = await llm.complete(task="story_card", messages=messages, temperature=0.3, json_object=True)
    except LLMError as exc:
        log_llm_error(db, task="story_card", provider=getattr(llm, "name", ""), error=str(exc))
        raise

    log_llm_result(db, result)
    return save_cards(db, normalize_stories(result.text, limit=count), resume_id=resume_id)


def pick_for_question(
    db: Session,
    *,
    question: str,
    topics: Sequence[str] = (),
    limit: int = 2,
) -> list[StoryCard]:
    cards = list_cards(db)
    if not cards:
        return []

    chosen = select_stories(refs_from_cards(cards), question=question, topics=topics, limit=limit)
    by_id = {card.id: card for card in cards}
    return [by_id[ref.id] for ref in chosen if ref.id in by_id]


def prompt_block_for(
    db: Session,
    *,
    question: str,
    topics: Sequence[str] = (),
    limit: int = 2,
) -> str:
    """面试官 prompt 里要插入的事实底稿（挑不出来时返回空串）。"""
    cards = pick_for_question(db, question=question, topics=topics, limit=limit)
    return story_card_prompt_block(refs_from_cards(cards))


def referenced_ids(db: Session, *, question: str, topics: Sequence[str] = (), limit: int = 2) -> list[int]:
    return [int(card.id or 0) for card in pick_for_question(db, question=question, topics=topics, limit=limit)]


def mark_used(db: Session, card_ids: Sequence[int]) -> None:
    """引用过就计数（用来提示"你最近总在讲同一段"）。"""
    touched = False
    for card_id in card_ids:
        card = db.get(StoryCard, card_id)
        if card is None:
            continue
        card.used_count += 1
        card.updated_at = utcnow()
        db.add(card)
        touched = True
    if touched:
        db.commit()


def coverage(db: Session) -> dict[str, int]:
    return story_coverage(refs_from_cards(list_cards(db)))


def starred_block(db: Session, *, limit: int = 3) -> str:
    """常驻底稿：主推的故事卡。

    语音面试里 config 在会话开始时固定，而面试官可能随时问到任何一段经历，
    所以这里给"主推的几张"而不是每轮重挑（文字模式那边是每轮动态挑的）。
    """
    cards = list_cards(db, starred_only=True)[:limit]
    if not cards:
        cards = list_cards(db)[:limit]
    return story_card_prompt_block(refs_from_cards(cards))


def missing_tags(db: Session) -> list[str]:
    """一张卡都没有的题型——被问到就答不上来，值得提前补。"""
    return missing_story_tags(refs_from_cards(list_cards(db)))
