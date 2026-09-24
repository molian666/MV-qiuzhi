"""系统设计白板的 IO 契约（M13）。

一个刻意的省略：维度摘要里**不含 `expects`**（该维度希望候选人提到的要点）。
那是评分参照，提前看到就把「你有没有想到」变成了「照着说」。
评估之后，明细里才会给出 `mentioned` / `missing`，那时是对照结果，不是提示。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DesignDimensionBriefOut(BaseModel):
    """维度摘要（不含评分参照）。"""

    dimension: str
    weight: str
    """core（及格线）或 normal / bonus"""


class DesignQuestionBriefOut(BaseModel):
    slug: str
    title: str
    difficulty: int
    tags: list[str]
    statement: str
    constraints: list[str]
    dimensions: list[DesignDimensionBriefOut]
    attempts: int
    last_verdict: str


class DesignQuestionOut(DesignQuestionBriefOut):
    open_questions: list[str]
    """题面故意不给、需要自己问清的约束"""
    followups: list[str]
    hints: list[str]


class DesignAttemptIn(BaseModel):
    question_slug: str
    writeup: str = Field(default="", max_length=20000)
    """文字说明——评估的主要依据"""
    scene: str = Field(default="", max_length=2_000_000)
    """Excalidraw 场景 JSON，原样存下来，前端能重新载入继续画"""
    target_id: int | None = None


class DesignMentionOut(BaseModel):
    point: str
    kind: Literal["stated", "inferred"] = "inferred"


class DesignDimensionOut(BaseModel):
    dimension: str
    weight: str
    score: Literal["strong", "ok", "weak", "missing"] = "missing"
    assessment: str = ""
    mentioned: list[DesignMentionOut] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class DesignReviewOut(BaseModel):
    dimensions: list[DesignDimensionOut] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    summary: str = ""
    verdict: Literal["strong", "ok", "partial", "weak", "unknown"] = "unknown"
    notes: str = ""


class DesignAttemptOut(BaseModel):
    id: int
    created_at: datetime
    question_slug: str
    question_title: str
    writeup: str
    scene: str
    scene_labels: list[str]
    review: dict
    verdict: str
    summary: str