"""题库的数据结构与判题约定（M13）。

题目形态统一为**函数式**：用户实现一个函数，判题器用 `tests` 里的 `args` 调用它、
和 `expected` 比对。这批题（尤其是机器视觉那批）都是纯计算函数，不依赖 I/O。

**多语言怎么表达**：`starters` / `function_names` / `signatures` 三个 dict 都以语言为键。
一道题只声明它真正支持的语言——CV 那批只声明 python，因为面试现场就是这么考的，
给个 JS 起始代码只会让人以为自己选错了。

**测试用例是跨语言共用的**：`args` 与 `expected` 都只用 JSON 能表达的类型。
Python 侧在沙箱里把嵌套 list 转成 `np.ndarray` 再调用（视觉题里的「图像」「boxes」
本来就是数组），标量保持原样（比如分类的 label 是 int）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CompareMode = Literal["exact", "unordered"]
TrackName = Literal["general", "vision"]


@dataclass(frozen=True, slots=True)
class AlgoTestCase:
    """一个测试用例。

    `args` 是要展开传给用户函数的参数列表（用 list 而非 tuple，方便直接 JSON 化）。
    `compare="unordered"` 用于顺序无关的题，那时按多重集比较。
    """

    args: list[Any]
    expected: Any
    note: str = ""
    compare: CompareMode = "exact"


@dataclass(frozen=True, slots=True)
class AlgoProblem:
    slug: str
    title: str
    difficulty: Literal["easy", "medium", "hard"]
    tags: list[str]
    statement: str
    starters: dict[str, str]
    function_names: dict[str, str]
    signatures: dict[str, str]
    tests: list[AlgoTestCase]
    hints: list[str]
    key_points: list[str] = field(default_factory=list)
    """评估时的对照要点：题目希望候选人主动谈到的方向。

    这些**不下发给用户**（`schemas/algo.py` 刻意不暴露），因为它们是评分参照，
    提前看到就把「你有没有想到」变成了「你抄得对不对」。
    """

    track: TrackName = "general"

    @property
    def languages(self) -> list[str]:
        return sorted(self.starters)

    def starter_for(self, language: str) -> str:
        return self.starters.get(language, "")

    def function_name_for(self, language: str) -> str:
        return self.function_names.get(language, "")

    def signature_for(self, language: str) -> str:
        return self.signatures.get(language, "")


def vision(problem: AlgoProblem) -> AlgoProblem:
    """给机器视觉题库的题目打上 track 标记（按来源标注，不用每道题手写）。"""
    return AlgoProblem(
        slug=problem.slug,
        title=problem.title,
        difficulty=problem.difficulty,
        tags=problem.tags,
        statement=problem.statement,
        starters=problem.starters,
        function_names=problem.function_names,
        signatures=problem.signatures,
        tests=problem.tests,
        hints=problem.hints,
        key_points=problem.key_points,
        track="vision",
    )
