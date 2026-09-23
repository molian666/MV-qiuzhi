"""岗位（含 JD 原文）：创建与查询。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..deps import get_session
from ..models.core import JobTarget
from ..schemas import JobTargetCreate, JobTargetOut

router = APIRouter(prefix="/api/targets", tags=["targets"])


@router.post("", response_model=JobTargetOut, status_code=201)
def create_target(payload: JobTargetCreate, db: Session = Depends(get_session)) -> JobTarget:
    target = JobTarget(**payload.model_dump())
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


@router.get("", response_model=list[JobTargetOut])
def list_targets(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[JobTarget]:
    statement = (
        select(JobTarget)
        .order_by(JobTarget.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    return list(db.exec(statement).all())


@router.get("/{target_id}", response_model=JobTargetOut)
def get_target(target_id: int, db: Session = Depends(get_session)) -> JobTarget:
    target = db.get(JobTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"岗位不存在：{target_id}")
    return target
