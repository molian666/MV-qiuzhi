"""岗位 / 简历 / 题库的对外模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobTargetCreate(BaseModel):
    company: str = ""
    title: str = ""
    level: str = ""
    jd_url: str = ""
    jd_text: str = ""
    tags: list[str] = Field(default_factory=list)


class JobTargetOut(JobTargetCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class ResumeCreate(BaseModel):
    name: str = ""
    content_text: str = ""
    variant_label: str = ""
    target_job_id: int | None = None
    parent_resume_id: int | None = None


class ResumeOut(ResumeCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int = 1
    created_at: datetime


class QuestionCreate(BaseModel):
    text: str
    type: str = "general"
    tags: list[str] = Field(default_factory=list)
    difficulty: int = 3
    reference_points: list[str] = Field(default_factory=list)
    sample_answer: str = ""
    source: str = "manual"


class QuestionOut(QuestionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
