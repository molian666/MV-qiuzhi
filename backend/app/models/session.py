"""会话、回合、音频资产与 LLM 调用日志。

`session_turn.start_ms/end_ms/audio_path` 在 M1 起承载带时间戳的音频（PLAN §9.1 第 1 条），
M0 文字模式也会写 start_ms/end_ms（用会话内相对时间），保证 M9 表达体检不用改表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class InterviewSession(SQLModel, table=True):
    """一场面试。status: created | live | finished | scored。"""

    __tablename__ = "interview_session"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    mode: str = Field(default="text")  # text | realtime
    status: str = Field(default="created", index=True)

    target_id: int | None = Field(default=None, foreign_key="job_target.id")
    resume_id: int | None = Field(default=None, foreign_key="resume.id")
    # M4 才有 interviewer_profile 表，这里先留列不加外键约束
    interviewer_profile_id: int | None = Field(default=None)

    plan: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    provider: str = ""
    cost_estimate: float | None = None
    summary: str = Field(default="", sa_column=Column(Text))

    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    duration_ms: int | None = Field(default=None)


class SessionTurn(SQLModel, table=True):
    """一个回合。speaker: interviewer | candidate | system。"""

    __tablename__ = "session_turn"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="interview_session.id", index=True)
    idx: int = 0

    speaker: str = Field(default="interviewer", index=True)
    text: str = Field(default="", sa_column=Column(Text))
    kind: str = ""  # opening | followup | new_topic | closing | answer | note
    topic: str = ""  # 本题主题，M3 起按主题聚合薄弱点
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # 模型原始输出，便于复盘

    start_ms: int | None = Field(default=None)
    end_ms: int | None = Field(default=None)
    audio_path: str | None = Field(default=None)
    asr_confidence: float | None = Field(default=None)

    is_followup: bool = False
    question_id: int | None = Field(default=None, foreign_key="question_bank.id")
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AudioAsset(SQLModel, table=True):
    """音频文件登记。kind: full | turn。"""

    __tablename__ = "audio_asset"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="interview_session.id", index=True)
    recording_id: int | None = Field(default=None, index=True)
    path: str = ""
    kind: str = "full"
    sample_rate: int = 16000
    duration_ms: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class LLMCall(SQLModel, table=True):
    """LLM 调用流水（PLAN §9.1 第 5 条：每次调用都带 task 标签）。

    用途：按任务换模型、核算成本、对比不同 prompt 的效果。
    """

    __tablename__ = "llm_call"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    task: str = Field(default="", index=True)  # interview_turn | scoring | resume_match | say_hi ...
    provider: str = ""
    model: str = ""
    session_id: int | None = Field(default=None, index=True)
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    ok: bool = True
    error: str = Field(default="", sa_column=Column(Text))
