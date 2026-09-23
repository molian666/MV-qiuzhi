"""数据模型总入口：导入所有模型模块，保证 SQLModel 元数据完整。"""

from .core import JobTarget, QuestionAttempt, QuestionBank, Resume
from .scoring import (
    ImprovementItem,
    MasteryEvent,
    ReviewTask,
    ScoreReport,
    SkillMastery,
    SkillTopic,
)
from .session import AudioAsset, InterviewSession, LLMCall, SessionTurn
from .system import AppSetting, ProviderConfig

__all__ = [
    "AppSetting",
    "AudioAsset",
    "ImprovementItem",
    "InterviewSession",
    "JobTarget",
    "LLMCall",
    "MasteryEvent",
    "ProviderConfig",
    "QuestionAttempt",
    "QuestionBank",
    "Resume",
    "ReviewTask",
    "ScoreReport",
    "SessionTurn",
    "SkillMastery",
    "SkillTopic",
]
