"""岗位、简历、题库。

注意（对应 PLAN §9.1 预留约束）：
- `Resume.content_text` / `Resume.variant_label` / `JobTarget.jd_text` 现在就带上，
  M8 的简历×JD 匹配、M10 的拒信归因与 JD 关键词热度都靠它们。
- 长文本一律 Text，JSON 字段一律 JSON 列，避免后续加维度要改表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class JobTarget(SQLModel, table=True):
    """目标岗位（含 JD 原文）。"""

    __tablename__ = "job_target"

    id: int | None = Field(default=None, primary_key=True)
    company: str = ""
    title: str = ""
    level: str = ""
    jd_url: str = ""
    jd_text: str = Field(default="", sa_column=Column(Text))
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Resume(SQLModel, table=True):
    """简历，多版本用 variant_label + parent_resume_id 组织（M8 起 A/B 统计）。"""

    __tablename__ = "resume"

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
    content_text: str = Field(default="", sa_column=Column(Text))
    version: int = 1
    variant_label: str = ""
    target_job_id: int | None = Field(default=None, foreign_key="job_target.id")
    parent_resume_id: int | None = Field(default=None, foreign_key="resume.id")
    ats_report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class QuestionBank(SQLModel, table=True):
    """题库。source: manual | uploaded | generated | extracted。"""

    __tablename__ = "question_bank"

    id: int | None = Field(default=None, primary_key=True)
    text: str = Field(default="", sa_column=Column(Text))
    type: str = Field(default="general", index=True)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    difficulty: int = 3
    reference_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    sample_answer: str = Field(default="", sa_column=Column(Text))
    source: str = "manual"
    created_at: datetime = Field(default_factory=utcnow, index=True)


class QuestionAttempt(SQLModel, table=True):
    """某道题在某场会话里的作答与得分（M3 起填 scores）。"""

    __tablename__ = "question_attempt"

    id: int | None = Field(default=None, primary_key=True)
    question_id: int | None = Field(default=None, foreign_key="question_bank.id")
    session_id: int | None = Field(default=None, foreign_key="interview_session.id", index=True)
    answer_text: str = Field(default="", sa_column=Column(Text))
    scores: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    asked_at: datetime = Field(default_factory=utcnow, index=True)
