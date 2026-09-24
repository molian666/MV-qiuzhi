"""题库汇总（M13）。

题库分两个来源：

- `bank_general.py`：通用算法题（哈希、双指针、二分、滑动窗口……），练的是写代码的手感；
- `bank_vision.py`：**机器视觉方向的题**（IoU、NMS、卷积、数值稳定的 softmax+CE），
  这批是 CV 面试真会让人现场手写的算子。

**为什么题库是代码常量而不是数据库**：题目是「内容」不是「用户数据」，
改题应该走代码评审。M2 做 Alembic 时如果需要用户自建题目，再把它搬进表——
那时有明确的对照物（现在这 10 道题）。
"""

from __future__ import annotations

from .bank_general import GENERAL_PROBLEMS
from .bank_vision import VISION_PROBLEMS
from .problem import AlgoProblem, AlgoTestCase, vision

PROBLEMS: tuple[AlgoProblem, ...] = GENERAL_PROBLEMS + tuple(
    vision(problem) for problem in VISION_PROBLEMS
)

_BY_SLUG = {problem.slug: problem for problem in PROBLEMS}


def list_problems(track: str | None = None) -> tuple[AlgoProblem, ...]:
    """按方向筛选题库。`track=None` 返回全部。"""
    if track is None:
        return PROBLEMS
    return tuple(problem for problem in PROBLEMS if problem.track == track)


def get_problem(slug: str) -> AlgoProblem | None:
    return _BY_SLUG.get(slug)


def supported_languages() -> list[str]:
    """题库里出现过的语言，用于前端提示与校验。"""
    languages: set[str] = set()
    for problem in PROBLEMS:
        languages.update(problem.languages)
    return sorted(languages)


__all__ = [
    "PROBLEMS",
    "AlgoProblem",
    "AlgoTestCase",
    "get_problem",
    "list_problems",
    "supported_languages",
]
