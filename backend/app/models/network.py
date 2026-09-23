"""人脉与学习计划的模型（M14）。

三张表：
- `contact`：人的档案（关系、公司、跟进节奏、关键日期）；
- `contact_interaction`：每次沟通一条记录——**时间线是这张表，不是 contact 上的一个字段**。
  因为"他答应过什么""下次什么时候联系"这些信息属于某一次具体的沟通，
  塞进联系人本身，第二句承诺就会把第一句覆盖掉；
- `learning_item`：学习清单项，`topic` 与 `skill_topic.name` 对齐，
  这样"学完这一项"能和 M5 的掌握度变化对上。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from ..clock import utcnow

CONTACT_RELATIONS: tuple[str, ...] = (
    "referral",
    "alumni",
    "hr",
    "headhunter",
    "ex_colleague",
    "interviewer",
    "other",
)

INTERACTION_KINDS: tuple[str, ...] = (
    "message",
    "call",
    "meeting",
    "referral",
    "interview",
    "thanks",
    "gift",
)

LEARNING_STATUSES: tuple[str, ...] = ("todo", "doing", "done", "dropped")

LEARNING_STATUS_LABELS: dict[str, str] = {
    "todo": "待开始",
    "doing": "进行中",
    "done": "已完成",
    "dropped": "已放弃",
}

LEARNING_SOURCES: tuple[str, ...] = ("weakness", "jd_gap", "manual")


class Contact(SQLModel, table=True):
    """通讯录里的一个人。"""

    __tablename__ = "contact"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="", index=True)
    relation: str = Field(default="other", index=True)
    company: str = ""
    title: str = ""

    channel: str = ""  # 微信 / 脉脉 / 邮箱 / 电话
    contact_info: str = ""
    note: str = Field(default="", sa_column=Column(Text))

    #: 跟进节奏（天）。0 = 用该关系的默认值
    follow_up_days: int = 0
    #: 便捷字段（时间线里最新一条会同步到这里，列表查询不用每次都 join）
    last_contact_at: datetime | None = Field(default=None, index=True)

    #: [{"label": "生日", "month": 5, "day": 12}]
    key_dates: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    #: 关联的投递 / offer（内推人往往对应一次具体投递）
    application_id: int | None = Field(default=None, foreign_key="application.id", index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class ContactInteraction(SQLModel, table=True):
    """一次沟通。时间线与"他答应过什么"都在这张表上。"""

    __tablename__ = "contact_interaction"

    id: int | None = Field(default=None, primary_key=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)

    at: datetime = Field(default_factory=utcnow, index=True)
    direction: str = "outbound"  # outbound | inbound
    kind: str = "message"

    summary: str = Field(default="", sa_column=Column(Text))
    #: 他答应过什么 / 你答应过什么
    promised: str = ""
    #: 明确约定的下次联系时间（比默认节奏准）
    next_follow_up_at: datetime | None = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=utcnow)


class LearningItem(SQLModel, table=True):
    """学习清单的一项。"""

    __tablename__ = "learning_item"

    id: int | None = Field(default=None, primary_key=True)
    title: str = ""
    #: weakness | jd_gap | manual
    source: str = Field(default="manual", index=True)
    #: 关联的技能主题（与 skill_topic.name 对齐 → 与掌握度联动）
    topic: str = Field(default="", index=True)

    priority: float = 0.0
    reason: str = Field(default="", sa_column=Column(Text))
    action: str = Field(default="", sa_column=Column(Text))
    hours: float = 0.0

    status: str = Field(default="todo", index=True)
    done_at: datetime | None = None

    #: 生成时的三块明细（便于回看"当时为什么排在前面"）
    breakdown: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
