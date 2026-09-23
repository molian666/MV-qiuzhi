"""话术生成服务（M8）：自我介绍 / 敏感题 / 反问清单。

生成的每一稿都**存库**（`answer_draft`），而不是算完就丢：
这些话术要反复打磨，留历史才看得出"改版之后是不是更顺了"。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .analysis.answer_bank import (
    SENSITIVE_QUESTIONS,
    build_questions_messages,
    build_self_intro_messages,
    build_sensitive_messages,
    char_budget,
    normalize_lines,
    normalize_self_intro,
    spoken_seconds,
)
from .models.story import AnswerDraft
from .providers.llm.base import LLMClient, LLMError
from .services import log_llm_error, log_llm_result
from .story_service import list_cards, refs_from_cards, resume_text_for


def list_drafts(db: Session, *, kind: str | None = None, limit: int = 50) -> list[AnswerDraft]:
    statement = select(AnswerDraft)
    if kind:
        statement = statement.where(AnswerDraft.kind == kind)
    statement = statement.order_by(AnswerDraft.id.desc()).limit(limit)  # type: ignore[attr-defined]
    return list(db.exec(statement).all())


def _card_refs(db: Session):
    return refs_from_cards(list_cards(db))


async def compose_self_intro(
    db: Session,
    *,
    llm: LLMClient,
    resume_id: int | None = None,
    seconds: int = 60,
    jd_text: str = "",
) -> AnswerDraft:
    resume_text = resume_text_for(db, resume_id)
    messages = build_self_intro_messages(
        resume_text=resume_text,
        stories=_card_refs(db),
        jd_text=jd_text,
        seconds=seconds,
    )

    try:
        result = await llm.complete(task="self_intro", messages=messages, temperature=0.5)
    except LLMError as exc:
        log_llm_error(db, task="self_intro", provider=getattr(llm, "name", ""), error=str(exc))
        raise
    log_llm_result(db, result)

    content = normalize_self_intro(result.text, seconds=seconds)
    draft = AnswerDraft(
        kind=f"self_intro_{seconds}",
        title=f"自我介绍（{seconds} 秒）",
        resume_id=resume_id,
        content=content,
        target_seconds=seconds,
        basis={
            "chars": len(content),
            "target_chars": char_budget(seconds),
            "estimated_seconds": spoken_seconds(content),
            "used_story_cards": [card.title for card in list_cards(db)][:6] if _card_refs(db) else [],
        },
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


async def compose_sensitive(
    db: Session,
    *,
    llm: LLMClient,
    kind: str,
    resume_id: int | None = None,
    jd_text: str = "",
) -> AnswerDraft:
    if kind not in SENSITIVE_QUESTIONS:
        raise ValueError(f"未知的敏感题类型：{kind}（可选：{'、'.join(SENSITIVE_QUESTIONS)}）")

    resume_text = resume_text_for(db, resume_id)
    messages = build_sensitive_messages(
        kind=kind,
        resume_text=resume_text,
        stories=_card_refs(db),
        jd_text=jd_text,
    )

    try:
        result = await llm.complete(task="sensitive_answer", messages=messages, temperature=0.4)
    except LLMError as exc:
        log_llm_error(db, task="sensitive_answer", provider=getattr(llm, "name", ""), error=str(exc))
        raise
    log_llm_result(db, result)

    label, question, strategy = SENSITIVE_QUESTIONS[kind]
    draft = AnswerDraft(
        kind=f"sensitive_{kind}",
        title=f"{label}：{question}",
        resume_id=resume_id,
        content=result.text.strip(),
        basis={
            "question": question,
            "strategy": strategy,
            "estimated_seconds": spoken_seconds(result.text),
            "note": "带方括号的空白需要你自己补——工具不会替你编经历",
        },
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


async def compose_questions(
    db: Session,
    *,
    llm: LLMClient,
    jd_text: str,
    company: str = "",
    resume_id: int | None = None,
    count: int = 5,
) -> AnswerDraft:
    resume_text = resume_text_for(db, resume_id)
    messages = build_questions_messages(
        jd_text=jd_text,
        company=company,
        resume_text=resume_text,
        count=count,
    )

    try:
        result = await llm.complete(task="questions_to_ask", messages=messages, temperature=0.6)
    except LLMError as exc:
        log_llm_error(db, task="questions_to_ask", provider=getattr(llm, "name", ""), error=str(exc))
        raise
    log_llm_result(db, result)

    lines = normalize_lines(result.text, limit=count)
    # 存成一行一条，前端直接渲染成清单
    content = "\n".join(lines)
    draft = AnswerDraft(
        kind="questions_to_ask",
        title=f"反问清单{f'（{company}）' if company else ''}",
        resume_id=resume_id,
        content=content,
        basis={"count": len(lines), "company": company},
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft
