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

    # M13：题源与占比。None = 让模型自由出题；
    # {"builtin": 2, "uploaded": 1} = 内置题占 2/3、自己上传的占 1/3
    question_mix: dict[str, float] | None = None
    #: 抽题筛选条件（在选中的来源里筛）
    question_role: Literal["development", "application", "implementation"] | None = None
    question_category: str | None = None
    question_max_difficulty: int | None = Field(default=None, ge=1, le=5)
    #: 抽几道题；不填则按 max_questions 取
    question_count: int | None = Field(default=None, ge=1, le=30)
    # M18：薄弱点面试。true 时抽题优先命中掌握度低的技能点（需配合 question_mix 从题库抽）
    weak_questions: bool = False

    # M14：拟真与反馈（四个独立开关，可叠加）
    #: 教练模式：每题答完，面试官先用一两句点评（要点 / 漏点 / 更优说法），再进下一题
    coach: bool = False
    #: 沉默施压：答完后面试官不急着接话，用"还有要补充的吗？"逼候选人补全
    silence_pressure: bool = False
    #: 现场突发：面试中途插入一道"现在在现场，设备突然…"的情境题，练临场排查
    scene: bool = False
    #: 简历深挖：开场后像真拿着简历逐行追问（角色 / 选型 / 数字 / 坑）
    resume_dive: bool = False


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
