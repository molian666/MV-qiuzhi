"""数据模型总入口：导入所有模型模块，保证 SQLModel 元数据完整。"""

from .apply import (
    Application,
    ApplicationEvent,
    ApplyRule,
    ApplyTask,
    Blacklist,
    ImportBatch,
    JobPosting,
    PlatformAccount,
)
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
    "Application",
    "ApplicationEvent",
    "ApplyRule",
    "ApplyTask",
    "AudioAsset",
    "Blacklist",
    "ImportBatch",
    "ImprovementItem",
    "InterviewSession",
    "InterviewerProfile",
    "JobPosting",
    "JobTarget",
    "LLMCall",
    "MasteryEvent",
    "PacingSample",
    "PlatformAccount",
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
