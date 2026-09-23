"""题库：创建、查询、删除（按 JD 生成题、录音抽题分别在 M2/M4）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..deps import get_session
from ..models.core import QuestionBank
from ..schemas import QuestionCreate, QuestionOut

router = APIRouter(prefix="/api/questions", tags=["questions"])


@router.post("", response_model=QuestionOut, status_code=201)
def create_question(payload: QuestionCreate, db: Session = Depends(get_session)) -> QuestionBank:
    question = QuestionBank(**payload.model_dump())
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


@router.post("/bulk", response_model=list[QuestionOut], status_code=201, summary="批量导入题目")
def bulk_create_questions(
    payload: list[QuestionCreate],
    db: Session = Depends(get_session),
) -> list[QuestionBank]:
    rows = [QuestionBank(**item.model_dump()) for item in payload]
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


@router.get("", response_model=list[QuestionOut])
def list_questions(
    type: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[QuestionBank]:
    statement = select(QuestionBank)
    if type:
        statement = statement.where(QuestionBank.type == type)
    statement = (
        statement.order_by(QuestionBank.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    return list(db.exec(statement).all())


@router.delete("/{question_id}", status_code=204)
def delete_question(question_id: int, db: Session = Depends(get_session)) -> None:
    question = db.get(QuestionBank, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail=f"题目不存在：{question_id}")
    db.delete(question)
    db.commit()
