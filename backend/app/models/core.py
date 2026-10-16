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
    """简历，多版本用 variant_label + parent_resume_id 组织（M8 起 A/B 统计）。

    source: manual（手动粘贴 / 快速新建）| upload（文件上传）；
    file_name / file_path 只在上传型简历上有值（原文件名与落盘位置，供追溯）。
    """

    __tablename__ = "resume"

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
    content_text: str = Field(default="", sa_column=Column(Text))
    version: int = 1
    variant_label: str = ""
    target_job_id: int | None = Field(default=None, foreign_key="job_target.id")
    parent_resume_id: int | None = Field(default=None, foreign_key="resume.id")
    ats_report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    source: str = "manual"
    file_name: str = ""
    file_path: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


class QuestionBank(SQLModel, table=True):
    """题库。source: manual | uploaded | generated | extracted。

    `question_type`：open（问答题，默认）| choice（选择题）。
    选择题额外有 `choice_options` / `correct_index` / `explanation`：
    它们只用于「生成 → 作答看解析 → 采纳入库」的自测闭环，抽题（今日训练 /
    模拟面试）不会抽到选择题——面试是问答场景，选项题不适用。
    """

    __tablename__ = "question_bank"

    id: int | None = Field(default=None, primary_key=True)
    text: str = Field(default="", sa_column=Column(Text))
    type: str = Field(default="general", index=True)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    difficulty: int = 3
    reference_points: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    sample_answer: str = Field(default="", sa_column=Column(Text))
    question_type: str = "open"
    """题型：open = 问答题（默认）；choice = 选择题（选项见下三列）。"""
    choice_options: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    """选择题的选项（通常 4 个，A/B/C/D）。问答题为空列表。"""
    correct_index: int | None = None
    """选择题正确答案的下标（0 起）。问答题为 None。"""
    explanation: str = Field(default="", sa_column=Column(Text))
    """解析：选择题是"为什么对/错"；问答题是答题思路补充。"""
    source: str = "manual"
    created_at: datetime = Field(default_factory=utcnow, index=True)


#: `QuestionBank.tags` 里的保留标记：**删除不是删行，而是打这个墓碑**。
#: 内置题库（M13）按题干幂等播种，硬删之后下一次播种会把它原样加回来，
#: 用户就会看到"删了又自己回来"——所以行留着，凡是给人看 / 拿去抽题的地方跳过它。
#: 常量放在这里而不是 `vision_service`：训练抽题（M5）与导出也要跳过已删除的题，
#: 它们本来就都依赖这个模型模块，不该为了一个字符串多出跨模块依赖。
DELETED_TAG = "已删除"


class QuestionAttempt(SQLModel, table=True):
    """某道题在某场会话里的作答与得分（M3 起填 scores）。"""

    __tablename__ = "question_attempt"

    id: int | None = Field(default=None, primary_key=True)
    question_id: int | None = Field(default=None, foreign_key="question_bank.id")
    session_id: int | None = Field(default=None, foreign_key="interview_session.id", index=True)
    answer_text: str = Field(default="", sa_column=Column(Text))
    scores: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    asked_at: datetime = Field(default_factory=utcnow, index=True)
