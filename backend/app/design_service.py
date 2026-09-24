"""系统设计白板的编排（M13）。

比算法题那条链路简单：题目是代码常量，作答存一张表，评估走一次 LLM。
真正需要想清楚的是**「什么算有效作答」**——

只画不写、只写不画都可以提交，但**全空不行**。原因：白板的价值之一是逼自己
把架构想清楚，文字说明则是能把方案讲出来的证据。两者都没有，就没有可评的东西。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from .models.design import DesignAttempt
from .providers.llm.base import LLMClient
from .vision.design import DesignQuestion, all_design_questions, get_design_question
from .vision.design_review import extract_scene_labels, review_design

#: 文字说明的最短长度：低于这个基本说明不了什么，评估也评不出东西
MIN_WRITEUP_LENGTH = 20


class DesignError(RuntimeError):
    """业务错误：题目不存在、作答内容不足以评估。"""


class DesignNotFoundError(DesignError):
    """题目不存在（→ 404）。"""


def _stats(db: Session) -> dict[str, dict[str, Any]]:
    """按题目聚合作答次数与最近判定，一次查完（题目只有几道，没必要逐题查）。"""
    stats: dict[str, dict[str, Any]] = {}
    rows = db.exec(select(DesignAttempt).order_by(DesignAttempt.created_at)).all()  # type: ignore[arg-type]
    for row in rows:
        entry = stats.setdefault(row.question_slug, {"attempts": 0, "last_verdict": ""})
        entry["attempts"] = int(entry["attempts"]) + 1
        if row.verdict:
            entry["last_verdict"] = row.verdict
    return stats


def _brief(question: DesignQuestion, stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entry = stats.get(question.slug, {})
    return {
        "slug": question.slug,
        "title": question.title,
        "difficulty": question.difficulty,
        "tags": list(question.tags),
        "statement": question.statement,
        "constraints": list(question.constraints),
        "dimensions": [
            {"dimension": item.dimension, "weight": item.weight} for item in question.rubric
        ],
        "attempts": int(entry.get("attempts", 0)),
        "last_verdict": str(entry.get("last_verdict", "")),
    }


def question_list(db: Session) -> list[dict[str, Any]]:
    stats = _stats(db)
    return [_brief(question, stats) for question in all_design_questions()]


def question_detail(db: Session, slug: str) -> dict[str, Any] | None:
    question = get_design_question(slug)
    if question is None:
        return None
    detail = _brief(question, _stats(db))
    detail.update(
        {
            "open_questions": list(question.open_questions),
            "followups": list(question.followups),
            "hints": list(question.hints),
        }
    )
    return detail


def require_question(slug: str) -> DesignQuestion:
    question = get_design_question(slug)
    if question is None:
        raise DesignNotFoundError(f"没有这道设计题：{slug}")
    return question


def validate_submission(*, writeup: str, scene: str) -> None:
    """两张都不足以评估时，早点报错比让模型去猜好。"""
    if not writeup.strip() and not scene.strip():
        raise DesignError("至少写一段方案说明，或者在白板上画出来——空作答没法评估")
    if writeup.strip() and len(writeup.strip()) < MIN_WRITEUP_LENGTH and not scene.strip():
        raise DesignError(
            f"方案说明太短（不到 {MIN_WRITEUP_LENGTH} 字）。"
            "可以先列一下你打算怎么设计，或者直接在白板上画出来。"
        )


async def submit_attempt(
    db: Session,
    *,
    llm: LLMClient,
    slug: str,
    writeup: str,
    scene: str = "",
    target_id: int | None = None,
) -> DesignAttempt:
    question = require_question(slug)
    validate_submission(writeup=writeup, scene=scene)

    labels = extract_scene_labels(scene)
    review = await review_design(llm, question=question, writeup=writeup, scene_labels=labels)

    attempt = DesignAttempt(
        question_slug=question.slug,
        writeup=writeup,
        scene=scene,
        scene_labels=labels,
        review=review.model_dump(),
        verdict=review.verdict,
        summary=review.summary,
        target_id=target_id,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def list_attempts(db: Session, *, slug: str | None = None, limit: int = 30) -> list[DesignAttempt]:
    statement = select(DesignAttempt).order_by(DesignAttempt.created_at.desc())  # type: ignore[attr-defined]
    if slug:
        statement = statement.where(DesignAttempt.question_slug == slug)
    return list(db.exec(statement.limit(limit)).all())


def get_attempt(db: Session, attempt_id: int) -> DesignAttempt | None:
    return db.get(DesignAttempt, attempt_id)


def attempt_out(attempt: DesignAttempt) -> dict[str, Any]:
    question = get_design_question(attempt.question_slug)
    return {
        "id": attempt.id,
        "created_at": attempt.created_at,
        "question_slug": attempt.question_slug,
        "question_title": question.title if question else attempt.question_slug,
        # scene 可能很大（几 MB），列表接口不该带上；详情才给
        "writeup": attempt.writeup,
        "scene": "",
        "scene_labels": attempt.scene_labels or [],
        "review": attempt.review,
        "verdict": attempt.verdict,
        "summary": attempt.summary,
    }


def attempt_detail_out(attempt: DesignAttempt) -> dict[str, Any]:
    data = attempt_out(attempt)
    data["scene"] = attempt.scene
    return data
