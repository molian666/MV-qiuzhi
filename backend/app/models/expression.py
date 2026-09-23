"""表达体检报告（M9）。

单独一张表而不是塞进 `score_report.dimension_scores`：
那个字段是「维度 → 分数」的结构（雷达图直接遍历它），
而表达体检有窗口曲线、停顿列表、逐条问题——塞进去会把雷达图弄崩。
所以分开放：`dimension_scores["表达"]` 只放分数，明细在这张表里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, JSON, Text
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class ExpressionSnapshot(SQLModel, table=True):
    """一场（或一份录音）的表达体检结果。"""

    __tablename__ = "expression_report"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="interview_session.id", index=True)
    recording_id: int | None = Field(default=None, index=True)

    #: 0–10 的表达分（与 M3 的 rubric 同尺度）
    overall: float = 0.0
    summary: str = Field(default="", sa_column=Column(Text))
    #: 完整明细（语速窗口 / 停顿 / 填充词 / 问题定位 / 练习建议）
    report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    has_audio: bool = False
    frame_ms: int = 50

    generated_at: datetime = Field(default_factory=utcnow, index=True)
