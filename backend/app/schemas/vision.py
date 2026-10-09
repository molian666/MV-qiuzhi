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


class VisionTopicChildOut(BaseModel):
    name: str
    count: int
    #: 完整分类串（`父 / 子`），直接拿它去筛题
    full: str


class VisionMarkTagOut(BaseModel):
    """一道题可以带多个标记标签（高频 / 易错 / 自定义），筛选器用。

    与 `VisionTopicOut` 的区别：主题是一道题一个，标签是一道题多个——
    所以筛选标签时是多选（同时带「高频」和「易错」的题才算命中）。
    """

    name: str
    count: int


class VisionTopicOut(BaseModel):
    """两级主题：父主题 + 它的子主题。

    为什么要两级：整理过的面经文档里，分类是"章节 + 题目括号"拼出来的
    （如「相机与图像基础 / 相机二开」），平铺出来是 80 多个标签、其中 40 多个只有 1 道题，
    根本没法用。界面上只平铺父主题，选中后再出子主题。
    """

    name: str
    count: int
    children: list[VisionTopicChildOut] = Field(default_factory=list)


class VisionMetaOut(BaseModel):
    roles: list[RoleOptionOut]
    categories: list[str]
    #: 两级主题（界面用这个筛；`categories` 保留给导出等旧调用方）
    topics: list[VisionTopicOut] = Field(default_factory=list)
    #: 题库里出现过的标记标签（高频 / 易错 / 自定义）及各自题数，筛选器用
    mark_tags: list[VisionMarkTagOut] = Field(default_factory=list)
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


class VisionDeleteIn(BaseModel):
    """批量删除。用 POST 传 id 列表而不是 `DELETE` 带 body——后者在部分代理与
    客户端上会被丢掉 body，而且 FastAPI 的 body-in-DELETE 支持一直是绕路走的。

    下限 1：空列表是调用方搞错了（前端在没有选中时不会发这个请求），
    宁可直接拒掉，也别返回一个"删了 0 条"的成功。
    """

    question_ids: list[int] = Field(min_length=1, max_length=500)


class VisionTagsIn(BaseModel):
    """给一道题添加 / 移除「标记」标签（高频、易错等题目性质，也可自定义）。

    一次请求可以同时加和删（先加后删）；移除一个不存在的标签是静默的，
    重复点击不会报错。只影响 `标记:` 那一类——分类与「上传:」来源
    是题目自身的属性，不归这个接口管。

    `add` / `remove` 里的取值在服务端会做清洗与保留字校验
    （空串、超过长度的、以「上传: / 标记:」开头的、系统保留的「已删除 / 草稿」都会被忽略）。
    """

    add: list[str] = Field(default_factory=list, max_length=20)
    remove: list[str] = Field(default_factory=list, max_length=20)


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
    tags: list[str] = Field(default_factory=list)
    """题目标签：来源批次、「上传:文件名」、分类，以及文档自带的标记
    （「高频」「易错」）。界面上据此显示这题是什么性质，也方便只练高频题。"""
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
