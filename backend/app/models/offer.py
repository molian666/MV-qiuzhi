"""Offer 与谈判的模型（M12）。

薪资**拆成字段**而不是存一个总数：月薪 × 月数、年终奖、股票、签字费是四件不同的事，
"总包 40 万"这种写法会把"月薪 2 万 × 16 个月"和"月薪 3.3 万 × 12 个月"
当成同一个东西——而它们的现金流、稳定性、离职时的损失完全不一样。

其它维度（成长、通勤、团队…）没有客观数据，只能是用户自己打分，所以用 JSON 存。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from ..clock import utcnow

OFFER_STATUSES: tuple[str, ...] = ("pending", "negotiating", "accepted", "declined", "expired")

OFFER_STATUS_LABELS: dict[str, str] = {
    "pending": "待定",
    "negotiating": "谈判中",
    "accepted": "已接受",
    "declined": "已拒绝",
    "expired": "已过期",
}


class Offer(SQLModel, table=True):
    """一个 offer（或还没谈完的口头 offer）。"""

    __tablename__ = "offer"

    id: int | None = Field(default=None, primary_key=True)
    company: str = Field(default="", index=True)
    title: str = ""
    #: 关联的投递记录（从投递一路跟到 offer，链路才完整）
    application_id: int | None = Field(default=None, foreign_key="application.id", index=True)

    # ---- 薪资结构（拆开存，见模块说明）----
    monthly_salary: float = 0.0
    months_per_year: float = 12.0
    annual_bonus: float = 0.0
    equity_per_year: float = 0.0
    #: 签字费只算首年
    signing_bonus: float = 0.0
    currency: str = "CNY"

    #: 其它维度的自评分（key 见 analysis/offer_matrix.DIMENSIONS）
    factors: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    status: str = Field(default="pending", index=True)
    #: offer 有效期（催你决策的那个日期）
    deadline: datetime | None = None
    note: str = Field(default="", sa_column=Column(Text))

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
