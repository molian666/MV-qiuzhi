"""导出的 IO 契约（M15）。

这两个模型原本寄居在 `schemas/career.py` 里——因为导出最早是为了导入职与长期的
数据而加的。但导出本身就是全量数据的出口，和职业规划模块没有关系；
放在这里才不会因为删掉某个业务模块而跟着消失。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExportSectionStat(BaseModel):
    key: str
    title: str
    rows: int


class ExportPreviewOut(BaseModel):
    generated_at: str
    sections: list[ExportSectionStat]
    formats: list[dict[str, str]]
    integrations: list[dict[str, Any]] = Field(default_factory=list)
