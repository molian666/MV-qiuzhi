"""评分报告、改进项与薄弱点掌握度（M3/M5 起写入内容，M0 先建表）。

`ScoreReport.dimension_scores` 用 JSON 存：M9 加「表达体检」、M11 加「抗压」、
M12 加「谈判」维度都只是加 key，不改表结构（PLAN §9.1 第 3 条）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class ScoreReport(SQLModel, table=True):
    """一场面试或一份录音的分析报告。"""

    __tablename__ = "score_report"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="interview_session.id", index=True)
    recording_id: int | None = Field(default=None, index=True)

    overall: float = 0.0
    dimension_scores: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    summary: str = Field(default="", sa_column=Column(Text))
    model: str = ""
    generated_at: datetime = Field(default_factory=utcnow, index=True)


class ImprovementItem(SQLModel, table=True):
    """一条具体问题 + 证据 + 建议 + 示范答案 + 立刻可做的练习。"""

    __tablename__ = "improvement_item"

    id: int | None = Field(default=None, primary_key=True)
    report_id: int = Field(foreign_key="score_report.id", index=True)

    severity: str = "medium"  # high | medium | low
    dimension: str = ""
    problem: str = Field(default="", sa_column=Column(Text))
    evidence_turn_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    suggestion: str = Field(default="", sa_column=Column(Text))
    corrected_answer: str = Field(default="", sa_column=Column(Text))
    drill: str = Field(default="", sa_column=Column(Text))
    topic_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))


class SkillTopic(SQLModel, table=True):
    """能力标签树：技能点 / 问题类型 / 软技能。"""

    __tablename__ = "skill_topic"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="", index=True)
    parent_id: int | None = Field(default=None, foreign_key="skill_topic.id")
    category: str = "skill"
    created_at: datetime = Field(default_factory=utcnow, index=True)


class SkillMastery(SQLModel, table=True):
    """掌握度（0-100），M5 起按 `new = clamp(old + k*(score-old))` 更新。"""

    __tablename__ = "skill_mastery"

    id: int | None = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="skill_topic.id", index=True)
    mastery: float = 0.0
    attempts: int = 0
    correct_hits: int = 0
    updated_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime | None = Field(default=None)


class ReviewTask(SQLModel, table=True):
    """间隔复习任务（SM-2，M5 起）。"""

    __tablename__ = "review_task"

    id: int | None = Field(default=None, primary_key=True)
    topic_id: int | None = Field(default=None, foreign_key="skill_topic.id", index=True)
    question_id: int | None = Field(default=None, foreign_key="question_bank.id")
    due_at: datetime = Field(default_factory=utcnow, index=True)
    interval_days: float = 1.0
    ease: float = 2.5
    done_at: datetime | None = Field(default=None)
