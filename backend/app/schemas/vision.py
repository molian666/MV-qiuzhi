"""机器视觉题库的 IO 契约（M13）。

列表接口**不返回答案**——不是为了防谁，而是列表页要能快速扫过几十道题，
把答案一起带出来会让响应臃肿。答案在详情里给全。

`VisionQuestionOut` 同时给出结构化的 `points` / `followups` / `pitfalls`，
而不是把答案揉成一大段 Markdown：要点要能当自评清单逐条勾，
追问和常见错误在界面上要能折叠，混成一段就做不到了。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: 自评。用三档而不是分数：自测时人能诚实区分的只有「答上来了/答了一半/没答上来」，
#: 让人打 1~10 分只会得到一堆无意义的 7 分。
SelfRating = Literal["got_it", "partial", "missed"]


class RoleOptionOut(BaseModel):
    key: str
    label: str
    description: str
    count: int


class VisionMetaOut(BaseModel):
    roles: list[RoleOptionOut]
    categories: list[str]
    total: int


class VisionQuestionBriefOut(BaseModel):
    id: int
    text: str
    role: str
    role_label: str
    category: str
    difficulty: int
    attempts: int
    """自测过几次"""
    last_rating: str
    """最近一次自评，没测过是空字符串"""


class VisionQuestionOut(VisionQuestionBriefOut):
    points: list[str]
    answer: str
    followups: list[str]
    pitfalls: list[str]


class VisionAttemptIn(BaseModel):
    rating: SelfRating
    note: str = Field(default="", max_length=2000)


class VisionAttemptOut(BaseModel):
    question_id: int
    rating: str
    attempts: int
    last_rating: str
    #: 各档位累计次数，用来在列表上显示「5 题里 3 题还不熟」这类进展
    tally: dict[str, int]
