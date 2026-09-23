"""数据模型总入口：导入所有模型模块，保证 SQLModel 元数据完整。"""

from .core import JobTarget, QuestionAttempt, QuestionBank, Resume
from .pacing import InterviewerProfile, PacingSample, Recording, TranscriptSegment
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
    "InterviewerProfile",
    "JobTarget",
    "LLMCall",
    "MasteryEvent",
    "PacingSample",
    "ProviderConfig",
    "QuestionAttempt",
    "QuestionBank",
    "Recording",
    "Resume",
    "ReviewTask",
    "ScoreReport",
    "SessionTurn",
    "SkillMastery",
    "SkillTopic",
    "TranscriptSegment",
]
