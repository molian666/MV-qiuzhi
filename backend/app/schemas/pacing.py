"""节奏学习的对外模型（M4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    #: 原文里的原始说话人标签（如「发言人1」）。角色是推断的场合下，
    #: 用户需要靠它核对"面试官到底是不是发言人1"。
    source_label: str = ""


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


class DocCheckOut(BaseModel):
    """文稿体检结果：上传前先告诉用户"这份文档能解析出什么"。"""

    #: 实际用来解码的编码（UTF-8 / GB18030 等）——让"乱码"这件事有据可查
    encoding: str = ""
    filename: str = ""
    total_lines: int = 0
    segments: int = 0
    labeled: int = 0
    interviewer: int = 0
    candidate: int = 0
    timestamped: int = 0
    looks_like_questions: int = 0
    #: 角色是不是推断出来的（原文只标了「发言人1/2」这类无角色语义的说话人）
    inferred_roles: bool = False
    role_note: str = ""
    #: 能不能用来学画像（至少要有一段面试官发言）
    usable: bool = False
    #: 一句话结论 + 逐条提醒，都能直接显示
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    #: 解析出的前几段，让用户肉眼核对切分对不对
    preview: list[TranscriptSegmentOut] = Field(default_factory=list)


class ProfileCreate(BaseModel):
    recording_ids: list[int] = Field(min_length=1)
    name: str = ""
    notes: str = ""
    #: new = 新建；append = 追加到已有画像；replace = 重新生成已有画像
    mode: Literal["new", "append", "replace"] = "new"
    profile_id: int | None = None
    """append / replace 时必填：指向要更新的那条画像"""


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
