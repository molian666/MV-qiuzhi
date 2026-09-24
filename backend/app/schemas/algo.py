"""算法题白板的 IO 契约（M13）。

一个刻意的省略：`AlgoProblemOut` **不含 `key_points`**。那是评估参照（题目希望
候选人主动谈到的方向），提前下发就把「你有没有想到」变成了「你抄得对不对」，
M13 的验收标准正好是前者。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AlgoProblemBriefOut(BaseModel):
    slug: str
    title: str
    difficulty: str
    tags: list[str]
    statement: str
    function_name: str
    signature: str


class AlgoTestCaseOut(BaseModel):
    index: int
    args: list[Any]
    expected: Any
    note: str = ""


class AlgoProblemOut(AlgoProblemBriefOut):
    starters: dict[str, str]
    tests: list[AlgoTestCaseOut]
    hints: list[str]


# --------------------------------------------------------------------------- #
# 提交与结果
# --------------------------------------------------------------------------- #


class AlgoCaseResultIn(BaseModel):
    """浏览器沙箱里单个用例的结果。"""

    index: int
    passed: bool
    actual: Any = None
    error: str = ""
    duration_ms: int | None = None


class AlgoAttemptIn(BaseModel):
    problem_slug: str
    language: Literal["javascript"] = "javascript"
    code: str = Field(min_length=1)
    duration_ms: int | None = None
    cases: list[AlgoCaseResultIn] = Field(default_factory=list)
    target_id: int | None = None


class AlgoTradeoffOut(BaseModel):
    """一处取舍。

    `kind` 是关键字段：`measured` 表示有测试结果支撑，`inferred` 表示模型从代码
    推断（没有实测）。两种混在一起陈述会把「可能有问题」说成「确实有问题」。
    """

    point: str
    assessment: str
    kind: Literal["measured", "inferred"] = "inferred"


class AlgoEdgeCaseOut(BaseModel):
    case: str
    status: Literal["covered", "missed", "untested"] = "untested"
    detail: str = ""


class AlgoReviewOut(BaseModel):
    approach_summary: str = ""
    approach_verdict: Literal["correct", "partial", "wrong", "unknown"] = "unknown"
    tradeoffs: list[AlgoTradeoffOut] = Field(default_factory=list)
    edge_cases: list[AlgoEdgeCaseOut] = Field(default_factory=list)
    complexity: dict[str, str] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    summary: str = ""
    notes: str = ""


class AlgoAttemptOut(BaseModel):
    id: int
    created_at: datetime
    problem_slug: str
    problem_title: str
    language: str
    code: str
    passed: int
    total: int
    duration_ms: int | None
    test_report: dict[str, Any]
    review: dict[str, Any]
    approach_verdict: str
    summary: str
