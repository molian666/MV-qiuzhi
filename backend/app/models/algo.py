"""算法题作答记录（M13）。

`algo_attempt` 不建外键指向题库：题库是代码里的常量（见 `algo/problems.py`），
没有对应的表。`problem_slug` 是弱引用 —— 题目改名不会破坏历史作答，
只是历史记录里那个 slug 可能查不到标题，前端要能容忍这种情况。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class AlgoAttempt(SQLModel, table=True):
    """一次算法题提交：代码 + 浏览器端判题结果 + 「思路—取舍—边界」评估。

    `test_report` 由前端沙箱回传。**服务端不重跑代码**：PLAN §9.2 明确要求
    判题在浏览器端沙箱里做（本机没有 docker，起不了隔离容器）。单用户场景下
    前端是可信的，而且就算不信，一份练习记录的判题结果也没有篡改动机。
    """

    __tablename__ = "algo_attempt"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    problem_slug: str = Field(index=True)
    language: str = Field(default="javascript")
    code: str = Field(default="", sa_column=Column(Text))

    passed: int = 0
    total: int = 0
    duration_ms: int | None = Field(default=None)

    test_report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    review: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # 从 review 里提升出来的两个可索引字段：列表页要按它们筛选与排序
    approach_verdict: str = Field(default="", index=True)
    summary: str = Field(default="", sa_column=Column(Text))

    target_id: int | None = Field(default=None, foreign_key="job_target.id")
