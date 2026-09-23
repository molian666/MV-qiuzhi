"""人脉与学习计划的对外模型（M14）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# 联系人
# --------------------------------------------------------------------------- #
class ContactIn(BaseModel):
    name: str = ""
    relation: str = "other"
    company: str = ""
    title: str = ""
    channel: str = ""
    contact_info: str = ""
    note: str = ""
    #: 0 = 用该关系的默认节奏
    follow_up_days: int = 0
    #: [{"label": "生日", "month": 5, "day": 12}]
    key_dates: list[dict[str, Any]] = Field(default_factory=list)
    application_id: int | None = None


class ContactOut(ContactIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_contact_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InteractionIn(BaseModel):
    at: datetime | None = None
    direction: str = "outbound"
    kind: str = "message"
    summary: str = ""
    promised: str = ""
    next_follow_up_at: datetime | None = None


class InteractionOut(InteractionIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int
    at: datetime
    created_at: datetime


class ContactDetailOut(BaseModel):
    contact: ContactOut
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)
    next_follow_up_at: datetime | None = None
    greeting: str | None = None


class FollowUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contact_id: int
    name: str
    relation: str
    relation_label: str
    company: str = ""
    channel: str = ""
    due_at: datetime | None = None
    days_overdue: int = 0
    urgency: str = "later"
    reason: str = ""
    suggested: str = ""
    promise: str = ""


# --------------------------------------------------------------------------- #
# 学习清单
# --------------------------------------------------------------------------- #
class LearningItemIn(BaseModel):
    title: str = ""
    topic: str = ""
    source: str = "manual"
    priority: float = 0
    reason: str = ""
    action: str = ""
    hours: float = 0
    status: str = "todo"
    breakdown: dict[str, Any] = Field(default_factory=dict)


class LearningItemOut(LearningItemIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    done_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LearningStatusIn(BaseModel):
    status: str


class LearningGenerateIn(BaseModel):
    limit: int = Field(default=8, ge=1, le=20)
    #: true 会清掉所有"待开始"的项；进行中与已完成的永远保留
    replace: bool = False
    resume_id: int | None = None


class LearningSummaryOut(BaseModel):
    total: int = 0
    done: int = 0
    doing: int = 0
    todo: int = 0
    hours_total: float = 0.0
    hours_done: float = 0.0
    progress: float = 0.0
    #: 一句话概览（"共 8 项、约 62 小时；最该补的是：高并发、分布式"）
    note: str = ""


class MasteryLinkOut(BaseModel):
    topic: str
    status: str = ""
    priority: float = 0.0
    mastery: float = 0.0
    #: 有没有掌握度记录——**没数据不等于掌握度 0**，前端据此显示「—」
    has_data: bool = False
    attempts: int = 0
    #: 学完但掌握度没动（或掌握度够高却还在待办）时的提醒
    note: str = ""


class NetworkOptionsOut(BaseModel):
    relations: list[dict[str, Any]] = Field(default_factory=list)
    interaction_kinds: list[dict[str, str]] = Field(default_factory=list)
    learning_statuses: list[dict[str, str]] = Field(default_factory=list)
    learning_sources: list[dict[str, str]] = Field(default_factory=list)
