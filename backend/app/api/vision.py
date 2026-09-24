"""机器视觉面试题库 API（M13）。

这是当前的主线功能：**题目 + 答案 + 自测**。

三个设计取舍：

- **建档与内容分离**：题目内容在 `app/vision/bank_*.py`（代码常量，保证质量可控），
  首次访问时幂等播种进 `question_bank` 表（自测记录需要外键指向它）。
- **列表不带答案**：列表页要能快速扫几十道题，答案在详情里给全。
- **自测用三档自评**：让人打 1~10 分只会得到一堆无意义的 7 分，
  而「答上来了 / 答了一半 / 没答上来」是人能诚实区分的。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..deps import get_session
from ..schemas.vision import (
    VisionAttemptIn,
    VisionAttemptOut,
    VisionMetaOut,
    VisionQuestionBriefOut,
    VisionQuestionOut,
)
from ..vision_service import VisionError, get_question, list_questions, meta, record_attempt

router = APIRouter(prefix="/api/vision", tags=["vision"])


@router.get("/meta", response_model=VisionMetaOut, summary="岗位方向与分类（给筛选器用）")
def read_meta(db: Session = Depends(get_session)) -> VisionMetaOut:
    return VisionMetaOut(**meta(db))  # type: ignore[arg-type]


@router.get("/questions", response_model=list[VisionQuestionBriefOut], summary="题目清单（不含答案）")
def read_questions(
    role: str | None = Query(default=None, description="development | application | implementation"),
    category: str | None = Query(default=None),
    max_difficulty: int | None = Query(default=None, ge=1, le=5),
    keyword: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[VisionQuestionBriefOut]:
    items = list_questions(
        db,
        role=role,
        category=category,
        max_difficulty=max_difficulty,
        keyword=keyword,
        limit=limit,
    )
    return [VisionQuestionBriefOut(**item) for item in items]  # type: ignore[arg-type]


@router.get("/questions/{question_id}", response_model=VisionQuestionOut, summary="题目详情：要点、参考答案、追问、常见错误")
def read_question(question_id: int, db: Session = Depends(get_session)) -> VisionQuestionOut:
    detail = get_question(db, question_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"没有这道题：{question_id}")
    return VisionQuestionOut(**detail)  # type: ignore[arg-type]


@router.post("/questions/{question_id}/attempt", response_model=VisionAttemptOut, summary="记录一次自测（自评）")
def create_attempt(
    question_id: int,
    payload: VisionAttemptIn,
    db: Session = Depends(get_session),
) -> VisionAttemptOut:
    try:
        result = record_attempt(db, question_id=question_id, rating=payload.rating, note=payload.note)
    except VisionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VisionAttemptOut(**result)  # type: ignore[arg-type]
