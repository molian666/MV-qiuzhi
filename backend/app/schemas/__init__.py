"""请求/响应模型（对外契约）。"""

from .core import (
    JobTargetCreate,
    JobTargetOut,
    QuestionCreate,
    QuestionOut,
    ResumeCreate,
    ResumeOut,
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
    "JobTargetCreate",
    "JobTargetOut",
    "QuestionCreate",
    "QuestionOut",
    "ResumeCreate",
    "ResumeOut",
    "SessionCreate",
    "SessionDetail",
    "SessionListItem",
    "SessionOut",
    "TurnOut",
]
