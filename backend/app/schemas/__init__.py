"""请求/响应模型（对外契约）。"""

from .core import (
    JobTargetCreate,
    JobTargetOut,
    QuestionCreate,
    QuestionOut,
    ResumeCreate,
    ResumeOut,
)
from .report import (
    DimensionScoreOut,
    EvidenceTurnOut,
    ImprovementItemOut,
    ReportSummaryOut,
    ScoreReportOut,
)
from .session import (
    AnswerIn,
    AnswerOut,
    SessionCreate,
    SessionDetail,
    SessionListItem,
    SessionOut,
    TurnOut,
)
from .training import (
    MasteryChangeOut,
    MasteryEventOut,
    MasteryItemOut,
    MasteryOverviewOut,
    TrainingGradeIn,
    TrainingGradeOut,
    TrainingQuestionOut,
)

__all__ = [
    "AnswerIn",
    "AnswerOut",
    "DimensionScoreOut",
    "EvidenceTurnOut",
    "ImprovementItemOut",
    "JobTargetCreate",
    "JobTargetOut",
    "MasteryChangeOut",
    "MasteryEventOut",
    "MasteryItemOut",
    "MasteryOverviewOut",
    "QuestionCreate",
    "QuestionOut",
    "ReportSummaryOut",
    "ResumeCreate",
    "ResumeOut",
    "ScoreReportOut",
    "SessionCreate",
    "SessionDetail",
    "SessionListItem",
    "SessionOut",
    "TrainingGradeIn",
    "TrainingGradeOut",
    "TrainingQuestionOut",
    "TurnOut",
]
