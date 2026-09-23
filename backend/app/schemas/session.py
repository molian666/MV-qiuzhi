"""会话与回合的对外模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    target_id: int | None = None
    resume_id: int | None = None
    # M4：选一个面试官画像，这场面试就会按那个节奏来（题型配比、追问深度、等待时长）
    interviewer_profile_id: int | None = None
    mode: Literal["text", "realtime"] = "text"
    max_questions: int | None = Field(default=None, ge=1, le=30)
    style: str | None = None

    # M11：压力模式（追问到底 / 打断 / 限时，可叠加）
    pressure: dict[str, Any] | None = None
    #: 面试官风格预设（technical / cross / manager / hr / behavioral / stress）
    style_key: str | None = None
    #: 多角色连环面：按轮次切换面试官，配了它就忽略 style_key
    panel: list[dict[str, Any]] | None = None
    #: 面试语言（zh / en）
    locale: str = "zh"


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
    #: 模型原始输出 + 编排附加信息（M11 起含当前面试官角色，前端据此显示"现在是谁在问"）
    meta: dict[str, Any] = Field(default_factory=dict)
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
    interviewer_profile_id: int | None = None
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
