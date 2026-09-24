"""机器视觉面试题的数据结构与汇总（M13）。

一道题包含四块内容，都是面试现场真用得上的：

- `points`：**答题要点**。既是参考答案的提纲，也是自测时的打分清单——
  「这几条我说到了几条」比「感觉答得还行」有用得多。
- `answer`：参考答案（Markdown）。分小节写，需要时带公式。
- `followups`：面试官在这个问题之后**大概率会追问**什么。答对主问题只是入场券，
  追问才是分水岭。
- `pitfalls`：**常见错误回答**。知道别人通常错在哪，比多背一条正确答案更能避坑。

按方向汇总在 `bank_development.py` / `bank_application.py` / `bank_implementation.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RoleName = Literal["development", "application", "implementation"]

#: 岗位方向与中文标签。机器视觉行业里这三类岗位的面试重点差别很大，
#: 所以题库第一级就按它分，而不是按「难不难」分。
ROLE_LABELS: dict[str, str] = {
    "development": "开发工程师",
    "application": "应用工程师",
    "implementation": "实施工程师",
    "uploaded": "我上传的",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "development": "算法选型与实现、底层算子、性能优化、标定的数学模型。偏「为什么会这样」。",
    "application": "相机/镜头/光源选型、精度评估、方案取舍。偏「给个需求你怎么定方案」。",
    "implementation": "现场部署、标定落地、故障排查、验收。偏「现场出问题你怎么下手」。",
    "uploaded": "你自己上传的题库文档解析出来的题。可以只练这些，也可以和内置题按比例混着练。",
}

#: 内置题库覆盖的三个岗位。`uploaded` 是来源而不是岗位，不该要求它有内置题。
BUILTIN_ROLES: tuple[str, ...] = ("development", "application", "implementation")


@dataclass(frozen=True, slots=True)
class VisionQuestion:
    #: 题干。写成面试官会问出来的样子，不要写成知识点标题。
    text: str
    role: RoleName
    #: 二级分类（相机与传感器 / 镜头与光学 / 光源与打光 / 标定 / 算法 / 软件 / 现场实施 ……）
    category: str
    #: 1（入门）～ 5（资深）。按「答得上来需要多少现场经验」估，不是按理论深度。
    difficulty: int
    points: list[str]
    answer: str
    followups: list[str]
    pitfalls: list[str]


def _bank() -> tuple[VisionQuestion, ...]:
    # 延迟导入：三个题库文件互相独立，这里只做汇总
    from .bank_application import APPLICATION_QUESTIONS
    from .bank_development import DEVELOPMENT_QUESTIONS
    from .bank_implementation import IMPLEMENTATION_QUESTIONS

    return DEVELOPMENT_QUESTIONS + APPLICATION_QUESTIONS + IMPLEMENTATION_QUESTIONS


_QUESTIONS: tuple[VisionQuestion, ...] | None = None


def all_questions() -> tuple[VisionQuestion, ...]:
    global _QUESTIONS
    if _QUESTIONS is None:
        _QUESTIONS = _bank()
        _check_unique(_QUESTIONS)
    return _QUESTIONS


def _check_unique(questions: tuple[VisionQuestion, ...]) -> None:
    """题干重复会让 `question_bank` 的播种去重失效——那样的 bug 很难查，
    所以在导入期就炸掉。"""
    seen: set[str] = set()
    for question in questions:
        key = question.text.strip()
        if key in seen:
            raise ValueError(f"题库里有重复题干：{key[:40]}")
        seen.add(key)


def get_question(text: str) -> VisionQuestion | None:
    for question in all_questions():
        if question.text == text:
            return question
    return None


def by_role(role: str) -> tuple[VisionQuestion, ...]:
    return tuple(question for question in all_questions() if question.role == role)


def roles() -> tuple[str, ...]:
    return tuple(ROLE_LABELS)
