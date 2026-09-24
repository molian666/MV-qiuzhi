"""系统设计白板 API（M13）。

`POST /api/design/attempts` 用 POST：它会把作答落库并调一次 LLM 评估。

**列表接口不返回 `scene`**：Excalidraw 的场景 JSON 可能有几 MB，
列表页带上它会让响应变得很重。详情里才给，用于重新载入画布。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..design_service import (
    DesignError,
    DesignNotFoundError,
    attempt_detail_out,
    attempt_out,
    get_attempt,
    list_attempts,
    question_detail,
    question_list,
    submit_attempt,
)
from ..deletion import DeletionError, delete_design_attempt
from ..deps import get_llm, get_session
from ..providers.llm.base import LLMClient
from ..schemas.design import DesignAttemptIn, DesignAttemptOut, DesignQuestionBriefOut, DesignQuestionOut

router = APIRouter(prefix="/api/design", tags=["design"])


@router.get("/questions", response_model=list[DesignQuestionBriefOut], summary="系统设计题清单")
def read_questions(db: Session = Depends(get_session)) -> list[DesignQuestionBriefOut]:
    return [DesignQuestionBriefOut(**item) for item in question_list(db)]  # type: ignore[arg-type]


@router.get("/questions/{slug}", response_model=DesignQuestionOut, summary="题面、约束与评分维度")
def read_question(slug: str, db: Session = Depends(get_session)) -> DesignQuestionOut:
    detail = question_detail(db, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"没有这道设计题：{slug}")
    return DesignQuestionOut(**detail)  # type: ignore[arg-type]


@router.post("/attempts", response_model=DesignAttemptOut, summary="提交作答：落库并按维度评估")
async def create_attempt(
    payload: DesignAttemptIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> DesignAttemptOut:
    try:
        attempt = await submit_attempt(
            db,
            llm=llm,
            slug=payload.question_slug,
            writeup=payload.writeup,
            scene=payload.scene,
            target_id=payload.target_id,
        )
    except DesignNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DesignError as exc:
        # 作答太短/全空：是输入问题，不是「找不到」
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DesignAttemptOut(**attempt_detail_out(attempt))  # type: ignore[arg-type]


@router.get("/attempts", response_model=list[DesignAttemptOut], summary="历史作答（不含画布内容）")
def read_attempts(
    slug: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[DesignAttemptOut]:
    return [
        DesignAttemptOut(**attempt_out(attempt))  # type: ignore[arg-type]
        for attempt in list_attempts(db, slug=slug, limit=limit)
    ]


@router.get("/attempts/{attempt_id}", response_model=DesignAttemptOut, summary="单次作答详情（含画布）")
def read_attempt(attempt_id: int, db: Session = Depends(get_session)) -> DesignAttemptOut:
    attempt = get_attempt(db, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail=f"没有这次作答：{attempt_id}")
    return DesignAttemptOut(**attempt_detail_out(attempt))  # type: ignore[arg-type]


@router.delete("/attempts/{attempt_id}", status_code=204, summary="删除一次作答")
def delete_attempt(attempt_id: int, db: Session = Depends(get_session)) -> None:
    try:
        delete_design_attempt(db, attempt_id)
    except DeletionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
