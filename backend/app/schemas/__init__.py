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

__all__ = [
    "AnswerIn",
    "AnswerOut",
    "DimensionScoreOut",
    "EvidenceTurnOut",
    "ImprovementItemOut",
    "JobTargetCreate",
    "JobTargetOut",
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
    "TurnOut",
]
