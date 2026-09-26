"""面试官画像 API（M4）：从录音学习画像、查看画像会怎么面你。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete
from sqlmodel import Session, select

from ..analysis.pacing import build_policy, policy_summary
from ..deps import get_llm, get_session, get_transcriber
from ..models.pacing import InterviewerProfile, PacingSample
from ..pacing_service import PacingError, build_profile, profile_samples
from ..providers.asr.base import BatchTranscriber
from ..providers.llm.base import LLMClient
from ..schemas.pacing import (
    PacingSampleOut,
    ProfileCreate,
    ProfileDetail,
    ProfileListItem,
)

router = APIRouter(prefix="/api/interviewer-profiles", tags=["profiles"])


def _detail(db: Session, profile: InterviewerProfile) -> ProfileDetail:
    samples = profile_samples(db, profile.id or 0)
    # 样本一起传：策略里要带上"他真实问过的原题"（前端细节页也直接展示这个）
    policy = build_policy(
        profile.params,
        profile_id=profile.id,
        profile_name=profile.name,
        samples=samples,
    )
    return ProfileDetail(
        id=profile.id or 0,
        name=profile.name,
        source=profile.source,
        style_tags=list(profile.style_tags or []),
        recording_ids=list(profile.recording_ids or []),
        created_at=profile.created_at,
        params=dict(profile.params or {}),
        policy=policy_summary(policy),
        samples=[PacingSampleOut.model_validate(sample) for sample in samples],
    )


@router.post("", response_model=ProfileDetail, status_code=201, summary="从 1..N 段录音学习一个面试官画像")
async def create_profile(
    payload: ProfileCreate,
    db: Session = Depends(get_session),
    transcriber: BatchTranscriber = Depends(get_transcriber),
    llm: LLMClient = Depends(get_llm),
) -> ProfileDetail:
    try:
        profile = await build_profile(
            db,
            recording_ids=payload.recording_ids,
            transcriber=transcriber,
            llm=llm,
            name=payload.name,
            notes=payload.notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PacingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _detail(db, profile)


@router.get("", response_model=list[ProfileListItem], summary="画像列表")
def list_profiles(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[ProfileListItem]:
    statement = (
        select(InterviewerProfile)
        .order_by(InterviewerProfile.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    items: list[ProfileListItem] = []
    for profile in db.exec(statement).all():
        params = dict(profile.params or {})
        source_features = params.get("source_features") or {}
        sample_count = len(profile_samples(db, profile.id or 0))
        items.append(
            ProfileListItem(
                id=profile.id or 0,
                name=profile.name,
                source=profile.source,
                style_tags=list(profile.style_tags or []),
                recording_ids=list(profile.recording_ids or []),
                created_at=profile.created_at,
                question_count=int(source_features.get("question_count") or 0),
                stress_level=str(params.get("stress") or ""),
                sample_count=sample_count,
            )
        )
    return items


@router.get("/{profile_id}", response_model=ProfileDetail, summary="画像详情（含节奏样本与策略摘要）")
def get_profile(profile_id: int, db: Session = Depends(get_session)) -> ProfileDetail:
    profile = db.get(InterviewerProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"画像不存在：{profile_id}")
    return _detail(db, profile)


@router.delete("/{profile_id}", status_code=204, summary="删除画像（连带节奏样本）")
def delete_profile(profile_id: int, db: Session = Depends(get_session)) -> None:
    profile = db.get(InterviewerProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"画像不存在：{profile_id}")

    # 用 SQL 级 delete 而不是 ORM 逐个删：先删子表样本、再删画像，顺序确定不会踩外键
    db.exec(delete(PacingSample).where(PacingSample.profile_id == profile_id))
    db.delete(profile)
    db.commit()
