"""报告的对外模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DimensionScoreOut(BaseModel):
    key: str
    label: str
    score: int
    comment: str = ""
    evidence_turn_ids: list[int] = Field(default_factory=list)


class EvidenceTurnOut(BaseModel):
    """引用的原话（前端直接展示，不用再拉一次会话）。"""

    turn_id: int
    speaker: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    audio_url: str | None = None


class ImprovementItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    severity: str
    dimension: str
    dimension_label: str
    problem: str
    suggestion: str
    corrected_answer: str
    drill: str
    topics: list[str] = Field(default_factory=list)
    evidence: list[EvidenceTurnOut] = Field(default_factory=list)


class ScoreReportOut(BaseModel):
    id: int
    session_id: int | None = None
    overall: float
    summary: str
    model: str = ""
    generated_at: datetime
    target_label: str = ""
    reused: bool = False
    dimensions: list[DimensionScoreOut] = Field(default_factory=list)
    items: list[ImprovementItemOut] = Field(default_factory=list)


class ReportSummaryOut(BaseModel):
    id: int
    session_id: int | None = None
    overall: float
    generated_at: datetime
    summary: str = ""
