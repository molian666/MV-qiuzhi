"""入职与长期的模型（M15）。

四组表：
- `onboarding_task`：入职清单。可勾选（done），每项带「为什么需要 + 去哪里办」——
  清单如果只写事项名，用户会不知道从哪开始，最后整张清单变成摆设；
- `okr_objective` / `okr_key_result`：试用期 OKR。进展不单独存，由 current/target 算出，
  避免出现「进度 80% 但 current 还是 0」这种自相矛盾的数据；
- `career_plan_item`：长期规划条目（晋升准备 / 跳槽时机 / 技能积累）；
- `salary_benchmark`：薪资基准。**只存来源与区间**，不存结论——
  「你应该要 40 万」这种话不该出现在数据里（纪律见 HANDOFF §六）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow

#: 入职清单的分类（顺序即展示顺序）
ONBOARDING_CATEGORIES: tuple[str, ...] = (
    "背调材料",
    "体检",
    "学历与离职证明",
    "社保与公积金",
    "离职交接",
    "租房与落户",
    "入职首日",
)

OKR_STATUSES: tuple[str, ...] = ("draft", "active", "achieved", "paused")

OKR_STATUS_LABELS: dict[str, str] = {
    "draft": "草稿",
    "active": "进行中",
    "achieved": "已达成",
    "paused": "暂缓",
}

PLAN_KINDS: tuple[str, ...] = ("promotion", "switch", "skill", "other")

PLAN_KIND_LABELS: dict[str, str] = {
    "promotion": "晋升准备",
    "switch": "跳槽时机",
    "skill": "技能积累",
    "other": "其它",
}

PLAN_STATUSES: tuple[str, ...] = ("idea", "doing", "done")

PLAN_STATUS_LABELS: dict[str, str] = {
    "idea": "想法",
    "doing": "推进中",
    "done": "已完成",
}


class OnboardingTask(SQLModel, table=True):
    """入职清单的一项。"""

    __tablename__ = "onboarding_task"

    id: int | None = Field(default=None, primary_key=True)
    category: str = Field(default="背调材料", index=True)
    title: str = ""
    #: 为什么需要：不解释清楚，清单就会被当成「反正HR会催」的空话
    why_needed: str = Field(default="", sa_column=Column(Text))
    #: 去哪里办：具体到 APP / 网站 / 找谁
    where_to_do: str = Field(default="", sa_column=Column(Text))

    note: str = Field(default="", sa_column=Column(Text))
    done: bool = Field(default=False, index=True)
    done_at: datetime | None = Field(default=None)
    sort_order: int = Field(default=100)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class OkrObjective(SQLModel, table=True):
    """试用期的一个目标。"""

    __tablename__ = "okr_objective"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="", sa_column=Column(Text))
    #: 周期，如「试用期 2026-10 ~ 2027-01」
    period: str = ""
    note: str = Field(default="", sa_column=Column(Text))
    status: str = Field(default="active", index=True)
    sort_order: int = Field(default=100)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class OkrKeyResult(SQLModel, table=True):
    """目标下的一条关键结果。进展 = current_value / target_value。"""

    __tablename__ = "okr_key_result"

    id: int | None = Field(default=None, primary_key=True)
    objective_id: int = Field(foreign_key="okr_objective.id", index=True)

    title: str = Field("", sa_column=Column(Text))
    unit: str = ""  # 次 / 篇 / 个 / % …
    target_value: float = 0.0
    current_value: float = 0.0
    #: 信心（0–100）：OKR 复盘的经典问法，和进展是两个维度
    confidence: int = Field(default=0)
    sort_order: int = Field(default=100)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CareerPlanItem(SQLModel, table=True):
    """长期规划的一条（晋升准备 / 跳槽时机 / 技能积累）。"""

    __tablename__ = "career_plan_item"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default="other", index=True)
    title: str = ""
    detail: str = Field(default="", sa_column=Column(Text))
    #: 计划日期用 date 而不是 datetime：它本来就没有「时刻」的含义，
    #: 用 datetime 存会引出一个无法回答的问题——「2027-06-30 该是哪个时区的零点？」
    target_date: date | None = Field(default=None)
    status: str = Field(default="idea", index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class SalaryBenchmark(SQLModel, table=True):
    """一条薪资基准记录：来自某个来源的参考区间。

    `verified=False` 是常态（同学打听、匿名社区都是未经验证的），
    展示时必须带着这个标注，绝不能模糊成「市场价」。
    """

    __tablename__ = "salary_benchmark"

    id: int | None = Field(default=None, primary_key=True)
    company: str = ""
    title: str = ""
    city: str = ""
    range_low: float = 0.0
    range_high: float = 0.0
    currency: str = "CNY"
    #: 时期，如「2026」「2026 Q3」——薪资会过期，没有时期的区间没有意义
    period: str = ""

    source: str = ""
    source_url: str = ""
    verified: bool = False
    note: str = Field(default="", sa_column=Column(Text))
    extra: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    recorded_at: datetime = Field(default_factory=utcnow, index=True)
