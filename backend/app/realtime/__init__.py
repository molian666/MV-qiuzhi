"""实时面试链路（M0 只含文字编排；M1 起加入音频与 VAD）。"""

from .interviewer import Interviewer, QuestionPlan, build_messages

__all__ = ["Interviewer", "QuestionPlan", "build_messages"]
