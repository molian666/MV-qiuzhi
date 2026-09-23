"""M8 简历智能与话术的对外模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JdKeywordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    word: str
    weight: float
    group: str = "other"
    group_label: str = ""
    mentions: int = 1
    sentence: str = ""


class AtsIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    severity: str
    kind: str
    message: str
    suggestion: str = ""


class MatchSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    target: str
    why: str
    where: str = ""
    draft: str = ""
    weight: float = 0.0


class MatchRequestIn(BaseModel):
    jd_text: str = ""
    job_title: str = ""
    #: 也可以直接从一条投递记录里带出 JD（M6/M10 落库的 application.jd_text）
    application_id: int | None = None


class MatchResultOut(BaseModel):
    score: float = 0.0
    grade: str = ""
    coverage: float = 0.0
    jd_keyword_count: int = 0
    covered: list[JdKeywordOut] = Field(default_factory=list)
    missing: list[JdKeywordOut] = Field(default_factory=list)
    extra: list[str] = Field(default_factory=list)
    missing_groups: list[str] = Field(default_factory=list)
    years_required: int | None = None
    years_actual: float | None = None
    years_ok: bool | None = None
    ats: list[AtsIssueOut] = Field(default_factory=list)
    suggestions: list[MatchSuggestionOut] = Field(default_factory=list)
    summary: str = ""


class ResumeCloneIn(BaseModel):
    name: str = ""
    variant_label: str = ""


class StoryCardIn(BaseModel):
    title: str = ""
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    metrics: str = ""
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    starred: bool = False
    source_resume_id: int | None = None


class StoryCardOut(StoryCardIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    used_count: int = 0
    created_at: datetime
    updated_at: datetime


class StoryListOut(BaseModel):
    items: list[StoryCardOut] = Field(default_factory=list)
    #: 每类问题各有几张故事卡
    coverage: dict[str, int] = Field(default_factory=dict)
    #: 一张都没有的题型——被问到就答不上来
    missing_tags: list[str] = Field(default_factory=list)
    tag_labels: dict[str, str] = Field(default_factory=dict)


class GenerateStoriesIn(BaseModel):
    resume_id: int | None = None
    count: int = Field(default=4, ge=1, le=8)


class SelfIntroIn(BaseModel):
    resume_id: int | None = None
    seconds: int = 60
    jd_text: str = ""


class SensitiveIn(BaseModel):
    kind: str
    resume_id: int | None = None
    jd_text: str = ""


class QuestionsIn(BaseModel):
    jd_text: str = ""
    company: str = ""
    resume_id: int | None = None
    count: int = Field(default=5, ge=1, le=10)


class AnswerDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    resume_id: int | None = None
    content: str
    target_seconds: int = 0
    basis: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class BandStatOut(BaseModel):
    label: str
    min_score: float
    count: int = 0
    responded: int = 0
    interviewed: int = 0
    offers: int = 0
    response_rate: float = 0.0
    interview_rate: float = 0.0


class MatchBandReportOut(BaseModel):
    resume_id: int | None = None
    total_records: int = 0
    matched_records: int = 0
    bands: list[BandStatOut] = Field(default_factory=list)
    recommendation: str = ""
    confidence_note: str = ""


class SensitiveQuestionOut(BaseModel):
    key: str
    label: str
    question: str
    strategy: str


class SelfIntroOptionOut(BaseModel):
    seconds: int
    label: str
    target_chars: int
