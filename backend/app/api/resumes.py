"""简历：创建与查询、多版本管理、以及 M8 的匹配诊断与 ATS 体检。"""

from __future__ import annotations

from dataclasses import asdict

from pathlib import Path
from re import sub as _re_sub
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlmodel import Session, select

from ..analysis.ats import check_ats
from ..analysis.resume_extract import UnsupportedResumeFile, extract_resume_text
from ..analysis.resume_match import extract_resume_hits, match as match_resume
from ..analysis.skill_dict import group_label
from ..config import get_settings
from ..deps import get_session
from ..models.apply import Application
from ..models.core import JobTarget, Resume
from ..schemas import ResumeCloneIn, ResumeCreate, ResumeOut
from ..schemas.resume_intel import (
    AtsIssueOut,
    JdKeywordOut,
    MatchRequestIn,
    MatchResultOut,
    MatchSuggestionOut,
)

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


def _load(db: Session, resume_id: int) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"简历不存在：{resume_id}")
    return resume


def _keyword_out(keyword) -> JdKeywordOut:  # noqa: ANN001 - 纯数据对象
    payload = asdict(keyword)
    payload["group_label"] = group_label(payload.get("group", "other"))
    return JdKeywordOut(**payload)


def _match_out(result) -> MatchResultOut:  # noqa: ANN001 - 纯数据对象
    return MatchResultOut(
        score=result.score,
        grade=result.grade,
        coverage=result.coverage,
        jd_keyword_count=result.jd_keyword_count,
        covered=[_keyword_out(item) for item in result.covered],
        missing=[_keyword_out(item) for item in result.missing],
        extra=result.extra,
        missing_groups=result.missing_groups,
        years_required=result.years_required,
        years_actual=result.years_actual,
        years_ok=result.years_ok,
        ats=[AtsIssueOut(**asdict(issue)) for issue in result.ats],
        suggestions=[MatchSuggestionOut(**asdict(item)) for item in result.suggestions],
        summary=result.summary,
    )


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


@router.post("/upload", response_model=ResumeOut, status_code=201, summary="上传简历文件（PDF/docx/txt/md）")
def upload_resume(
    file: UploadFile = File(...),
    name: str = Form(""),
    target_job_id: int | None = Form(None),
    db: Session = Depends(get_session),
) -> Resume:
    """保存原文件 + 提取正文入库。原文件名保留在 file_name / file_path 里供追溯。"""
    settings = get_settings()
    original_name = file.filename or "简历"
    suffix = Path(original_name).suffix.lower()

    resume_dir = settings.upload_dir / "resumes"
    resume_dir.mkdir(parents=True, exist_ok=True)

    # 字节只读一次：解析用一份、落盘用同一份，避免重复读 file.file
    payload = file.file.read() or b""
    if not payload:
        raise HTTPException(status_code=422, detail="文件是空的")

    tmp_path = resume_dir / f"tmp_{uuid4().hex[:12]}{suffix}"
    tmp_path.write_bytes(payload)
    try:
        try:
            text = extract_resume_text(tmp_path)
        except UnsupportedResumeFile as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if not text:
        raise HTTPException(
            status_code=422,
            detail="没有从文件里提取到正文：请检查文件内容是否为空，或是否为扫描件/图片型 PDF",
        )

    if target_job_id is not None and db.get(JobTarget, target_job_id) is None:
        raise HTTPException(status_code=404, detail=f"岗位不存在：{target_job_id}")

    # 名字默认取原文件名（去扩展名）；同名族内版本递增
    base_name = (name.strip() or Path(original_name).stem).strip()
    family = list(db.exec(select(Resume).where(Resume.name == base_name)).all())
    resume = Resume(
        name=base_name,
        content_text=text,
        version=len(family) + 1,
        target_job_id=target_job_id,
        source="upload",
        file_name=original_name,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    # 原文件正式落盘（带 resume.id，同名文件不会互相覆盖）
    safe = _re_sub(r"[^\w\-.\u4e00-\u9fff]+", "_", original_name)
    final_path = resume_dir / f"{resume.id}_{safe}"
    final_path.write_bytes(payload)
    resume.file_path = str(final_path)
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


@router.post("/{resume_id}/match", response_model=MatchResultOut, summary="简历 × JD 匹配诊断")
def diagnose_match(
    resume_id: int,
    payload: MatchRequestIn,
    db: Session = Depends(get_session),
) -> MatchResultOut:
    resume = _load(db, resume_id)

    jd_text = payload.jd_text.strip()
    if not jd_text and payload.application_id is not None:
        application = db.get(Application, payload.application_id)
        if application is None:
            raise HTTPException(status_code=404, detail=f"投递记录不存在：{payload.application_id}")
        jd_text = (application.jd_text or "").strip()

    if not jd_text:
        raise HTTPException(
            status_code=422,
            detail="没有 JD 正文可比：贴一段 JD，或指定一条带 JD 的投递记录",
        )

    return _match_out(match_resume(resume.content_text, jd_text, job_title=payload.job_title))


@router.post("/{resume_id}/ats", response_model=list[AtsIssueOut], summary="格式与可读性体检（不需要 JD）")
def diagnose_ats(resume_id: int, db: Session = Depends(get_session)) -> list[AtsIssueOut]:
    resume = _load(db, resume_id)
    hits = extract_resume_hits(resume.content_text)
    return [AtsIssueOut(**asdict(issue)) for issue in check_ats(resume.content_text, skill_count=len(hits))]


@router.post("/{resume_id}/clone", response_model=ResumeOut, status_code=201, summary="另存为一个简历版本")
def clone_resume(
    resume_id: int,
    payload: ResumeCloneIn,
    db: Session = Depends(get_session),
) -> Resume:
    source = _load(db, resume_id)
    name = (payload.name or source.name).strip() or source.name

    # 版本号在**同名族**里递增（M6 的 A/B 统计按 variant_label 分组，按名字归族最直观）
    family = [row for row in db.exec(select(Resume)).all() if row.name == name]
    clone = Resume(
        name=name,
        content_text=source.content_text,
        version=max([row.version for row in family], default=0) + 1,
        variant_label=payload.variant_label or f"{source.variant_label or '主简历'} 副本",
        target_job_id=source.target_job_id,
        parent_resume_id=source.id,
        ats_report=dict(source.ats_report or {}),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone


@router.get("/{resume_id}/versions", response_model=list[ResumeOut], summary="同一份简历的所有版本")
def list_versions(resume_id: int, db: Session = Depends(get_session)) -> list[Resume]:
    resume = _load(db, resume_id)
    root_id = resume.parent_resume_id or resume.id

    rows = list(db.exec(select(Resume).order_by(Resume.version)).all())  # type: ignore[arg-type]
    return [row for row in rows if row.id == root_id or row.parent_resume_id == root_id]
