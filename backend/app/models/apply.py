"""投递与投递执行的表（M6 建表，M10 才真正驱动投递）。

为什么岗位（job_posting）与投递（application）必须分开放（PLAN §9.1 第 6 条）：
- 同一个岗位可能被多次看到/多次投递（换简历版本再投）；
- 抓取到的岗位里大部分**不会**被投，混在一起会让"投递漏斗"统计被污染；
- M10 的半自动投递需要"先有候选岗位、再产生投递"，这两步在数据上是两件事。

M6 只用到 job_posting（可空）、application、application_event、import_batch；
其余四张表（apply_rule / apply_task / platform_account / blacklist）按 §9.1 第 7 条先建好，
这样 M10 只用加逻辑、不用改数据结构。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow

#: 投递状态。顺序即漏斗顺序；rejected/ghosted/withdrawn 是终止态。
APPLICATION_STATUSES: tuple[str, ...] = (
    "applied",
    "viewed",
    "screening",
    "interview1",
    "interview2",
    "hr",
    "offer",
    "rejected",
    "ghosted",
    "withdrawn",
)

TERMINAL_STATUSES: tuple[str, ...] = ("rejected", "ghosted", "withdrawn")


class JobPosting(SQLModel, table=True):
    """抓取/导入到的岗位（可能还没投）。"""

    __tablename__ = "job_posting"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    platform: str = Field(default="other", index=True)
    external_id: str = Field(default="", index=True)
    company: str = ""
    title: str = ""
    jd_text: str = Field(default="", sa_column=Column(Text))
    jd_url: str = ""
    salary_range: str = ""
    city: str = ""

    experience_req: str = ""
    degree_req: str = ""
    company_size: str = ""
    company_stage: str = ""
    industry: str = ""

    hr_name: str = ""
    hr_active: bool | None = Field(default=None)
    is_headhunter: bool | None = Field(default=None)

    matched_score: float | None = Field(default=None)
    filter_verdict: str = ""  # M10 的过滤链结论
    fetched_at: datetime = Field(default_factory=utcnow)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Application(SQLModel, table=True):
    """我投出去的一次投递。"""

    __tablename__ = "application"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    job_posting_id: int | None = Field(default=None, foreign_key="job_posting.id")
    company: str = Field(default="", index=True)
    title: str = ""
    channel: str = Field(default="other", index=True)  # boss|liepin|zhilian|51job|lagou|referral|other
    jd_url: str = ""
    jd_text: str = Field(default="", sa_column=Column(Text))
    resume_version: str = ""

    applied_at: datetime = Field(default_factory=utcnow, index=True)
    status: str = Field(default="applied", index=True)
    salary_range: str = ""
    city: str = ""

    source: str = "manual"  # manual|csv|extension|imap|apply_bot
    external_id: str = Field(default="", index=True)
    #: CSV/扩展导入时的去重键（company|title|applied_at 的短哈希）
    dedup_key: str = Field(default="", index=True)
    note: str = Field(default="", sa_column=Column(Text))


class ApplicationEvent(SQLModel, table=True):
    """状态流转留痕：谁在什么时候把状态从 A 改成了 B。"""

    __tablename__ = "application_event"

    id: int | None = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    from_status: str = ""
    to_status: str = ""
    raw_ref: str = ""  # 来源线索（邮件 id / 页面 URL / "手动"）


class ApplyRule(SQLModel, table=True):
    """M10 的过滤链规则（现在只建表，不驱动行为）。"""

    __tablename__ = "apply_rule"

    id: int | None = Field(default=None, primary_key=True)
    platform: str = Field(default="", index=True)
    name: str = ""
    kind: str = Field(default="keyword", index=True)
    enabled: bool = True
    priority: int = 100
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class ApplyTask(SQLModel, table=True):
    """M10 的待投队列（现在只建表）。status: pending|approved|sent|skipped|failed|blocked"""

    __tablename__ = "apply_task"

    id: int | None = Field(default=None, primary_key=True)
    job_posting_id: int | None = Field(default=None, foreign_key="job_posting.id", index=True)
    status: str = Field(default="pending", index=True)
    say_hi_text: str = Field(default="", sa_column=Column(Text))
    resume_image_path: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)
    approved_at: datetime | None = Field(default=None)
    sent_at: datetime | None = Field(default=None)
    attempts: int = 0
    error: str = Field(default="", sa_column=Column(Text))


class PlatformAccount(SQLModel, table=True):
    """M10 的平台账号状态（登录态、每日额度、熔断）。"""

    __tablename__ = "platform_account"

    id: int | None = Field(default=None, primary_key=True)
    platform: str = Field(default="", index=True)
    login_state_path: str = ""
    last_login_at: datetime | None = Field(default=None)
    daily_sent: int = 0
    daily_limit: int = 30
    #: 熔断到什么时候为止（检测到风控/验证码就停到当天结束）
    circuit_broken_until: datetime | None = Field(default=None)
    note: str = ""


class Blacklist(SQLModel, table=True):
    """不想再看到的公司/岗位/HR。"""

    __tablename__ = "blacklist"

    id: int | None = Field(default=None, primary_key=True)
    platform: str = Field(default="", index=True)
    company: str = Field(default="", index=True)
    job_title: str = ""
    hr_name: str = ""
    reason: str = "manual"  # manual|auto_rejected|unresponsive
    added_at: datetime = Field(default_factory=utcnow, index=True)


class ImportBatch(SQLModel, table=True):
    """一次导入的流水：来源、行数、去重指纹。"""

    __tablename__ = "import_batch"

    id: int | None = Field(default=None, primary_key=True)
    channel: str = ""
    source: str = Field(default="csv", index=True)  # csv|extension|imap|manual|apply_bot
    at: datetime = Field(default_factory=utcnow, index=True)
    rows: int = 0
    inserted: int = 0
    skipped: int = 0
    updated: int = 0
    dedup_key_hash: str = ""
    note: str = Field(default="", sa_column=Column(Text))
