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
from .design import DesignAttempt
from .expression import ExpressionSnapshot
from .offer import OFFER_STATUS_LABELS, OFFER_STATUSES, Offer
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
from .story import STORY_TAG_ORDER, STORY_TAGS, AnswerDraft, StoryCard
from .system import AppSetting, ProviderConfig

__all__ = [
    "STORY_TAG_ORDER",
    "STORY_TAGS",
    "AppSetting",
    "AnswerDraft",
    "Application",
    "ApplicationEvent",
    "ApplyRule",
    "ApplyTask",
    "AudioAsset",
    "Blacklist",
    "DesignAttempt",
    "ExpressionSnapshot",
    "ImportBatch",
    "ImprovementItem",
    "InterviewSession",
    "InterviewerProfile",
    "JobPosting",
    "JobTarget",
    "LLMCall",
    "MasteryEvent",
    "OFFER_STATUSES",
    "OFFER_STATUS_LABELS",
    "Offer",
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
    "StoryCard",
    "TranscriptSegment",
]
