"""故事卡与话术库的模型（M8）。

`story_card` 是这个模块的地基：模拟面试里"讲一个你最有成就感的项目"这类问题，
如果每次都靠模型现编，答案会与你的真实经历越飘越远。
故事卡是你自己的**事实底稿**——模型只能引用它，不能改写事实。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from ..clock import utcnow

#: 故事能对应的问题类型（模拟时按题型挑卡；也让"我缺哪类故事"一眼可见）
STORY_TAGS: dict[str, str] = {
    "achievement": "成就与亮点",
    "challenge": "难题攻坚",
    "failure": "失败复盘",
    "conflict": "冲突与协作",
    "leadership": "推动与带人",
    "growth": "成长与学习",
    "efficiency": "效率与优化",
    "quality": "质量与稳定",
}

STORY_TAG_ORDER: tuple[str, ...] = tuple(STORY_TAGS.keys())


class StoryCard(SQLModel, table=True):
    """一张 STAR 故事卡。"""

    __tablename__ = "story_card"

    id: int | None = Field(default=None, primary_key=True)
    #: 一句话标题，如「订单系统重构」
    title: str = Field(default="", index=True)

    # STAR 四段（situation/task/action/result）
    situation: str = Field(default="", sa_column=Column(Text))
    task: str = Field(default="", sa_column=Column(Text))
    action: str = Field(default="", sa_column=Column(Text))
    result: str = Field(default="", sa_column=Column(Text))
    #: 可核对的数字（单独拎出来，面试时最容易被追问的就是它）
    metrics: str = ""

    #: 能回答哪类问题（STORY_TAGS 的 key）
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    #: 关联的技能主题（与 skill_topic.name 对齐，用于薄弱点联动）
    topics: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    source_resume_id: int | None = Field(default=None, foreign_key="resume.id")
    #: 是否主推（模拟时优先引用）
    starred: bool = Field(default=False, index=True)
    #: 使用次数（引用过就 +1，方便看出哪张卡最常用）
    used_count: int = 0

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class AnswerDraft(SQLModel, table=True):
    """话术草稿：自我介绍、敏感题、反问清单。

    存下来而不是每次现生成，原因很实在——这些话术需要你反复打磨，
    而"每次都是新的一段"会让你没法比较改版效果。
    """

    __tablename__ = "answer_draft"

    id: int | None = Field(default=None, primary_key=True)
    #: self_intro_30 | self_intro_60 | self_intro_180 | sensitive_leave | sensitive_gap | ...
    kind: str = Field(default="", index=True)
    title: str = ""
    #: 适用岗位（空表示通用）
    job_target_id: int | None = Field(default=None, foreign_key="job_target.id")
    resume_id: int | None = Field(default=None, foreign_key="resume.id")
    content: str = Field(default="", sa_column=Column(Text))
    #: 目标时长（秒），用于自我介绍计时练
    target_seconds: int = 0
    #: 生成依据（简历 / 故事卡 / JD），便于回头看"这段话是从哪来的"
    basis: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
