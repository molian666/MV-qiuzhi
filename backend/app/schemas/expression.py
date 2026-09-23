"""表达体检的对外模型（M9）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FillerHitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    word: str
    count: int
    weight: float
    strong: bool
    weighted: float


class WindowMetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_ms: int
    end_ms: int
    chars: float = 0.0
    cpm: float = 0.0
    rms: float = 0.0


class PauseSpanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_ms: int
    duration_ms: int
    kind: str = "normal"
    turn_index: int | None = None
    before_text: str = ""
    after_text: str = ""
    seconds: float = 0.0


class TurnIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    severity: str = "info"
    at_ms: int = 0
    excerpt: str = ""
    detail: str = ""
    turn_index: int | None = None


class ExpressionReportOut(BaseModel):
    id: int
    session_id: int | None = None
    recording_id: int | None = None
    overall: float = 0.0
    summary: str = ""
    has_audio: bool = False
    frame_ms: int = 50
    generated_at: datetime

    speaking_ms: int = 0
    chars: int = 0
    overall_cpm: float = 0.0
    filler_total: int = 0
    filler_weighted: float = 0.0
    filler_per_kchar: float = 0.0
    #: 样本够不够——不够时密度类指标只展示、不参与评分
    reliable: bool = True
    fillers: list[FillerHitOut] = Field(default_factory=list)
    pauses: list[PauseSpanOut] = Field(default_factory=list)
    longest_pause: PauseSpanOut | None = None
    windows: list[WindowMetricOut] = Field(default_factory=list)
    pace_cv: float = 0.0
    volume_cv: float = 0.0
    tension: float = 0.0
    issues: list[TurnIssueOut] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    #: 整场录音地址（前端据此跳播；没有音频时为 None）
    audio_url: str | None = None
