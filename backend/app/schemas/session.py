"""会话与回合的对外模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    target_id: int | None = None
    resume_id: int | None = None
    mode: Literal["text", "realtime"] = "text"
    max_questions: int | None = Field(default=None, ge=1, le=30)
    style: str | None = None


class AnswerIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idx: int
    speaker: str
    text: str
    kind: str = ""
    topic: str = ""
    is_followup: bool = False
    start_ms: int | None = None
    end_ms: int | None = None
    created_at: datetime
    # 该回合的音频切片地址（M1 起有；前端回放用），由 api 层按 audio_path 推导
    audio_url: str | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mode: str
    status: str
    target_id: int | None = None
    resume_id: int | None = None
    provider: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    # 生成报告后回填摘要：历史列表不用打开报告就能看个大概
    summary: str = ""
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None


class SessionListItem(SessionOut):
    target_label: str = ""
    question_count: int = 0
    answer_count: int = 0


class SessionDetail(SessionOut):
    target_label: str = ""
    question_count: int = 0
    answer_count: int = 0
    turns: list[TurnOut] = Field(default_factory=list)


class AnswerOut(BaseModel):
    session: SessionDetail
    question: TurnOut | None = None
    finished: bool = False
    message: str = ""
