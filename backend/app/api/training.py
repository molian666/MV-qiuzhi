"""薄弱点与训练 API（M5）。

`POST /api/training/next` 用 POST 而不是 GET：缺题时它会**真的生成题目并写库**，
有副作用；GET 只用于读掌握度这类纯查询。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..deps import get_llm, get_session
from ..learning_service import due_topic_ids, mastery_overview, topic_history
from ..providers.llm.base import LLMClient
from ..schemas.training import (
    MasteryChangeOut,
    MasteryEventOut,
    MasteryItemOut,
    MasteryOverviewOut,
    TrainingGradeIn,
    TrainingGradeOut,
    TrainingQuestionOut,
)
from ..training_service import TrainingError, build_training_set, grade_training_answer

router = APIRouter(tags=["training"])


@router.get("/api/skills/mastery", response_model=MasteryOverviewOut, summary="掌握度清单（最该练的排最前）")
def read_mastery(db: Session = Depends(get_session)) -> MasteryOverviewOut:
    items = mastery_overview(db)
    average = round(sum(float(item["mastery"]) for item in items) / len(items), 2) if items else 0.0
    return MasteryOverviewOut(
        items=[MasteryItemOut(**item) for item in items],  # type: ignore[arg-type]
        high_risk_count=sum(1 for item in items if item["high_risk"]),
        average_mastery=average,
        due_count=len(due_topic_ids(db)),
    )


@router.get("/api/skills/{topic_id}/history", response_model=list[MasteryEventOut], summary="某个能力的掌握度变化历史")
def read_history(
    topic_id: int,
    limit: int = Query(default=60, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[MasteryEventOut]:
    return [MasteryEventOut.model_validate(row) for row in topic_history(db, topic_id, limit=limit)]


@router.post("/api/training/next", response_model=list[TrainingQuestionOut], summary="组一套今日训练")
async def next_training(
    limit: int = Query(default=4, ge=1, le=10),
    weak_count: int = Query(default=3, ge=1, le=10),
    target_id: int | None = Query(default=None),
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> list[TrainingQuestionOut]:
    questions = await build_training_set(
        db,
        llm=llm,
        limit=limit,
        weak_count=min(weak_count, limit),
        target_id=target_id,
    )
    return [TrainingQuestionOut(**asdict(question)) for question in questions]


@router.post("/api/training/answer", response_model=TrainingGradeOut, summary="提交训练作答并即时批改")
async def answer_training(
    payload: TrainingGradeIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> TrainingGradeOut:
    try:
        grade = await grade_training_answer(
            db,
            llm=llm,
            question_id=payload.question_id,
            answer=payload.answer,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TrainingGradeOut(
        question_id=grade.question_id,
        attempt_id=grade.attempt_id,
        score=grade.score,
        comment=grade.comment,
        better_answer=grade.better_answer,
        mastery=[MasteryChangeOut(**asdict(change)) for change in grade.mastery],
    )
