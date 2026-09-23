"""薄弱点与训练的对外模型（M5）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MasteryItemOut(BaseModel):
    topic_id: int
    name: str
    category: str = "skill"
    mastery: float
    attempts: int
    correct_hits: int
    updated_at: datetime | None = None
    high_risk: bool = False


class MasteryOverviewOut(BaseModel):
    items: list[MasteryItemOut] = Field(default_factory=list)
    high_risk_count: int = 0
    average_mastery: float = 0.0
    due_count: int = 0


class MasteryEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    mastery_before: float
    mastery_after: float
    score: float
    source: str
    note: str = ""


class TrainingQuestionOut(BaseModel):
    question_id: int
    text: str
    type: str = "training"
    difficulty: int = 3
    tags: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    origin: str = "bank"
    kind: str = "weak"
    # 打分依据（薄弱度/岗位匹配/到期/难度/题型罚项），前端用来说明"为什么推荐这题"
    reason: dict[str, float] = Field(default_factory=dict)


class TrainingGradeIn(BaseModel):
    question_id: int
    answer: str = Field(min_length=1, max_length=8000)


class MasteryChangeOut(BaseModel):
    topic_id: int
    name: str
    before: float
    after: float


class TrainingGradeOut(BaseModel):
    question_id: int
    attempt_id: int
    score: int
    comment: str = ""
    better_answer: str = ""
    mastery: list[MasteryChangeOut] = Field(default_factory=list)
