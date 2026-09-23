"""节奏学习的对外模型（M4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RecordingCreate(BaseModel):
    title: str = ""
    kind: str = "real_interview"
    company: str = ""
    role: str = ""
    #: 对话文稿：Mock 转写模式下的"文字替身"（音频可选，文稿必需）
    raw_text: str = ""


class TranscriptSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    idx: int
    speaker: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    kind: str
    company: str
    role: str
    status: str
    duration_ms: int | None = None
    error: str = ""
    created_at: datetime
    has_audio: bool = False
    has_text: bool = False


class RecordingDetail(RecordingOut):
    segments: list[TranscriptSegmentOut] = Field(default_factory=list)


class ProfileCreate(BaseModel):
    recording_ids: list[int] = Field(min_length=1)
    name: str = ""
    notes: str = ""


class PacingSampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_type: str = "general"
    topic: str = ""
    question: str = ""
    ask_duration_ms: int = 0
    wait_ms: int | None = None
    answer_ms: int | None = None
    followup_count: int = 0
    interrupted: bool = False


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source: str
    style_tags: list[str] = Field(default_factory=list)
    recording_ids: list[int] = Field(default_factory=list)
    created_at: datetime


class ProfileListItem(ProfileOut):
    question_count: int = 0
    stress_level: str = ""
    sample_count: int = 0


class ProfileDetail(ProfileOut):
    params: dict[str, Any] = Field(default_factory=dict)
    #: policy_summary：把画像翻译成"这场面试会怎么进行"，供前端展示
    policy: dict[str, Any] = Field(default_factory=dict)
    samples: list[PacingSampleOut] = Field(default_factory=list)
