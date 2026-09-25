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


class VisionSourceStatOut(BaseModel):
    """题源统计。前端拿它来配「面试时各来源占多少」。"""

    key: str
    """builtin（内置题库）或 uploaded（自己上传的）"""
    label: str
    count: int


class VisionMetaOut(BaseModel):
    roles: list[RoleOptionOut]
    categories: list[str]
    total: int
    sources: list[VisionSourceStatOut] = Field(default_factory=list)
    #: 待采纳的草稿题数。它们不在 sources 统计里（还没进抽题池）
    draft_count: int = 0


class VisionGenerateIn(BaseModel):
    role: Literal["development", "application", "implementation"] = "development"
    count: int = Field(default=4, ge=1, le=12)
    category: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    #: new = 新建批次；append = 在既有批次上**继续生成**；replace = 丢掉这批**重新生成**
    mode: Literal["new", "append", "replace"] = "new"
    batch: str | None = None
    """append / replace 时必填"""
    note: str = Field(default="", max_length=500)


class VisionAdoptIn(BaseModel):
    """按题采纳草稿。整批采纳走 `POST /drafts/{batch}/adopt`，这里只针对选中的题。"""

    question_ids: list[int] = Field(min_length=1, max_length=200)
    """补充要求，如「多出一些打光相关的」"""


class VisionGenerateOut(BaseModel):
    batch: str
    mode: str
    added: int
    skipped: int
    """与题库已有题目重复、被跳过的数量"""
    drafts: list["VisionQuestionBriefOut"] = Field(default_factory=list)
    note: str = ""


class VisionDraftOut(BaseModel):
    batch: str
    count: int
    source: str


class VisionImportOut(BaseModel):
    batch: str
    source: str
    filename: str
    role: str
    added: int
    """新入库的题数"""
    skipped: int
    """因为题干已存在而跳过的数量"""
    total: int
    """文件里解析出的总数"""
    source_format: str
    warnings: list[str] = Field(default_factory=list)


class VisionUploadOut(BaseModel):
    batch: str
    source: str
    filename: str
    role: str
    count: int


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
