"""人脉与学习计划 API（M14）。

路由里有两处**顺序敏感**的地方：`/api/follow-ups` 与 `/api/learning/summary` 这类
固定路径必须写在 `/{id}` 之前，否则会被当成 id 去解析（contact_id="follow-ups" 直接 422）。
我在路径设计上直接避开了：固定路径不带 `{id}` 前缀。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..analysis.networking import interaction_options, relation_options
from ..deps import get_session
from ..models.network import (
    LEARNING_STATUSES,
    LEARNING_STATUS_LABELS,
    LearningItem,
)
from ..network_service import (
    add_interaction,
    contact_detail,
    create_contact,
    delete_contact,
    delete_interaction,
    delete_learning,
    follow_ups,
    generate_path,
    learning_summary,
    learning_vs_mastery,
    list_contacts,
    list_learning,
    path_summary,
    update_contact,
    update_learning,
)
from ..schemas.network import (
    ContactDetailOut,
    ContactIn,
    ContactOut,
    FollowUpOut,
    InteractionIn,
    InteractionOut,
    LearningGenerateIn,
    LearningItemIn,
    LearningItemOut,
    LearningStatusIn,
    LearningSummaryOut,
    MasteryLinkOut,
    NetworkOptionsOut,
)

router = APIRouter(tags=["network"])


def _detail_out(payload: dict) -> ContactDetailOut:
    return ContactDetailOut(
        contact=ContactOut.model_validate(payload["contact"]),
        timeline=payload["timeline"],
        health=payload["health"],
        next_follow_up_at=payload["next_follow_up_at"],
        greeting=payload["greeting"],
    )


# --------------------------------------------------------------------------- #
# 选项
# --------------------------------------------------------------------------- #
@router.get("/api/network/options", response_model=NetworkOptionsOut, summary="人脉与学习的选项")
def read_options() -> NetworkOptionsOut:
    return NetworkOptionsOut(
        relations=relation_options(),
        interaction_kinds=interaction_options(),
        learning_statuses=[{"key": key, "label": LEARNING_STATUS_LABELS[key]} for key in LEARNING_STATUSES],
        learning_sources=[
            {"key": "weakness", "label": "来自薄弱点"},
            {"key": "jd_gap", "label": "来自 JD 缺口"},
            {"key": "manual", "label": "手动添加"},
        ],
    )


# --------------------------------------------------------------------------- #
# 联系人
# --------------------------------------------------------------------------- #
@router.get("/api/contacts", response_model=list[ContactOut], summary="通讯录")
def read_contacts(
    relation: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[ContactOut]:
    return [ContactOut.model_validate(contact) for contact in list_contacts(db, relation=relation)]


@router.post("/api/contacts", response_model=ContactOut, status_code=201, summary="新增联系人")
def create(payload: ContactIn, db: Session = Depends(get_session)) -> ContactOut:
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="名字是必填的（不然跟进的时候想不起是谁）")
    return ContactOut.model_validate(create_contact(db, payload=payload.model_dump()))


@router.get("/api/contacts/{contact_id}", response_model=ContactDetailOut, summary="联系人详情（含时间线）")
def read_contact(contact_id: int, db: Session = Depends(get_session)) -> ContactDetailOut:
    payload = contact_detail(db, contact_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"联系人不存在：{contact_id}")
    return _detail_out(payload)


@router.put("/api/contacts/{contact_id}", response_model=ContactOut, summary="修改联系人")
def update(contact_id: int, payload: ContactIn, db: Session = Depends(get_session)) -> ContactOut:
    contact = update_contact(db, contact_id, payload=payload.model_dump())
    if contact is None:
        raise HTTPException(status_code=404, detail=f"联系人不存在：{contact_id}")
    return ContactOut.model_validate(contact)


@router.delete("/api/contacts/{contact_id}", status_code=204, summary="删除联系人")
def remove(contact_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_contact(db, contact_id):
        raise HTTPException(status_code=404, detail=f"联系人不存在：{contact_id}")


@router.post(
    "/api/contacts/{contact_id}/interactions",
    response_model=InteractionOut,
    status_code=201,
    summary="记一次沟通",
)
def create_interaction(
    contact_id: int,
    payload: InteractionIn,
    db: Session = Depends(get_session),
) -> InteractionOut:
    row = add_interaction(db, contact_id, payload=payload.model_dump())
    if row is None:
        raise HTTPException(status_code=404, detail=f"联系人不存在：{contact_id}")
    return InteractionOut.model_validate(row)


@router.delete("/api/interactions/{interaction_id}", status_code=204, summary="删除一次沟通记录")
def remove_interaction(interaction_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_interaction(db, interaction_id):
        raise HTTPException(status_code=404, detail=f"沟通记录不存在：{interaction_id}")


#: 固定路径，放在 /api/contacts/{id} 之外，避免被当成 id
@router.get("/api/follow-ups", response_model=list[FollowUpOut], summary="该跟进的人（逾期优先）")
def read_follow_ups(
    horizon_days: int = Query(default=14, ge=1, le=180),
    include_later: bool = Query(default=False),
    db: Session = Depends(get_session),
) -> list[FollowUpOut]:
    rows = follow_ups(db, horizon_days=horizon_days, include_later=include_later)
    return [FollowUpOut.model_validate(row) for row in rows]


# --------------------------------------------------------------------------- #
# 学习清单
# --------------------------------------------------------------------------- #
@router.get("/api/learning", response_model=list[LearningItemOut], summary="学习清单")
def read_learning(
    status: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[LearningItemOut]:
    return [LearningItemOut.model_validate(item) for item in list_learning(db, status=status)]


@router.get("/api/learning/summary", response_model=LearningSummaryOut, summary="完成度概览")
def read_learning_summary(db: Session = Depends(get_session)) -> LearningSummaryOut:
    payload = learning_summary(db)
    payload["note"] = path_summary(db).get("note", "")
    return LearningSummaryOut(**payload)


@router.get("/api/learning/insight", response_model=list[MasteryLinkOut], summary="「学完了」与「掌握了」的对照")
def read_learning_insight(db: Session = Depends(get_session)) -> list[MasteryLinkOut]:
    return [MasteryLinkOut(**row) for row in learning_vs_mastery(db)]


@router.post("/api/learning/generate", response_model=list[LearningItemOut], status_code=201, summary="按薄弱点与 JD 缺口生成清单")
def generate(
    payload: LearningGenerateIn,
    db: Session = Depends(get_session),
) -> list[LearningItemOut]:
    items = generate_path(db, limit=payload.limit, replace=payload.replace, resume_id=payload.resume_id)
    return [LearningItemOut.model_validate(item) for item in items]


@router.post("/api/learning", response_model=LearningItemOut, status_code=201, summary="手动加一项")
def create_learning(payload: LearningItemIn, db: Session = Depends(get_session)) -> LearningItemOut:
    item = LearningItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return LearningItemOut.model_validate(item)


@router.put("/api/learning/{item_id}", response_model=LearningItemOut, summary="更新进度")
def update_learning_item(
    item_id: int,
    payload: LearningItemIn,
    db: Session = Depends(get_session),
) -> LearningItemOut:
    item = update_learning(db, item_id, payload=payload.model_dump())
    if item is None:
        raise HTTPException(status_code=404, detail=f"学习项不存在：{item_id}")
    return LearningItemOut.model_validate(item)


@router.delete("/api/learning/{item_id}", status_code=204, summary="删除学习项")
def remove_learning(item_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_learning(db, item_id):
        raise HTTPException(status_code=404, detail=f"学习项不存在：{item_id}")
