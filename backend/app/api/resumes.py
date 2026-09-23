"""简历：创建与查询（多版本 A/B 统计在 M10）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..deps import get_session
from ..models.core import JobTarget, Resume
from ..schemas import ResumeCreate, ResumeOut

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.post("", response_model=ResumeOut, status_code=201)
def create_resume(payload: ResumeCreate, db: Session = Depends(get_session)) -> Resume:
    if payload.target_job_id is not None and db.get(JobTarget, payload.target_job_id) is None:
        raise HTTPException(status_code=404, detail=f"岗位不存在：{payload.target_job_id}")

    # 同名简历自动递增版本号
    existing = db.exec(select(Resume).where(Resume.name == payload.name)).all()
    version = len(existing) + 1

    resume = Resume(**payload.model_dump(), version=version)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.get("", response_model=list[ResumeOut])
def list_resumes(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[Resume]:
    statement = (
        select(Resume)
        .order_by(Resume.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    return list(db.exec(statement).all())


@router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: int, db: Session = Depends(get_session)) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"简历不存在：{resume_id}")
    return resume
