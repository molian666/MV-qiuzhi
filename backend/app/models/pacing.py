"""节奏学习相关的表：录音、转写段落、面试官画像、节奏样本。

为什么录音与转写要单独成表（而不是塞进 interview_session）：
这些录音来自**真实面试**，与"我的模拟会话"是两回事——它们只用来学习面试官的节奏。
混在一张表里会让"我的面试历史"和"面试官的风格样本"互相污染。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class Recording(SQLModel, table=True):
    """一段上传的真实面试录音（或粘贴的对话文稿）。

    status: uploaded | transcribing | analyzing | done | failed
    """

    __tablename__ = "recording"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    title: str = ""
    kind: str = Field(default="real_interview", index=True)  # real_interview | practice
    company: str = ""
    role: str = ""

    # 音频文件（可选：没有音频、只有文稿时 path 为空）
    path: str = ""
    duration_ms: int | None = Field(default=None)

    # 文字替身：Mock 转写模式下直接读它（用户粘贴/听写的面试对话）
    raw_text: str = Field(default="", sa_column=Column(Text))

    status: str = Field(default="uploaded", index=True)
    error: str = Field(default="", sa_column=Column(Text))
    notes: str = Field(default="", sa_column=Column(Text))


class TranscriptSegment(SQLModel, table=True):
    """转写段落：一段连续发言（带说话人与时间）。"""

    __tablename__ = "transcript_segment"

    id: int | None = Field(default=None, primary_key=True)
    recording_id: int = Field(foreign_key="recording.id", index=True)
    idx: int = 0

    speaker: str = Field(default="unknown", index=True)  # interviewer | candidate | unknown
    text: str = Field(default="", sa_column=Column(Text))

    start_ms: int | None = Field(default=None)
    end_ms: int | None = Field(default=None)
    confidence: float | None = Field(default=None)


class InterviewerProfile(SQLModel, table=True):
    """面试官画像：从录音里量化出来的节奏特征。"""

    __tablename__ = "interviewer_profile"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    name: str = ""
    source: str = "extracted"  # extracted | preset | manual
    recording_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    style_tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 运行时策略的唯一事实来源，结构见 docs/PLAN.md §6.2 / app/analysis/pacing.py
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    notes: str = Field(default="", sa_column=Column(Text))


class PacingSample(SQLModel, table=True):
    """单个问题的节奏样本（画像就是这些样本的统计）。"""

    __tablename__ = "pacing_sample"

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int | None = Field(default=None, foreign_key="interviewer_profile.id", index=True)
    recording_id: int | None = Field(default=None, foreign_key="recording.id", index=True)

    question_type: str = "general"
    topic: str = ""
    question: str = Field(default="", sa_column=Column(Text))

    ask_duration_ms: int = 0  # 面试官问这一题说了多久
    wait_ms: int | None = Field(default=None)  # 问完到候选人开口的间隔
    answer_ms: int | None = Field(default=None)
    followup_count: int = 0  # 这一题后面跟了几层追问
    interviewer_wpm: int | None = Field(default=None)
    candidate_ratio: float | None = Field(default=None)  # 候选人发言占这一题总时长的比例
    interrupted: bool = False  # 面试官是否在候选人说话时插话
