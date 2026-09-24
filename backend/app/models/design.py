"""系统设计题的作答记录（M13）。

这是**唯一需要新建表的地方**：题库复用了 M0 预留的 `question_bank`，
但系统设计的作答含「白板内容」这个大字段，塞进现有表无处可放。

`scene` 存 Excalidraw 的场景 JSON，`writeup` 存文字说明。
**评估主要依据文字说明**，但画布里的文字标签会被抽出来一起给模型——
候选人在图上写的模块名、箭头标注本身就是方案的一部分，不利用起来很浪费。
（不传图是因为评估用的是文本模型，把元素里的文字提出来比传位图可靠。）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from ..clock import utcnow


class DesignAttempt(SQLModel, table=True):
    """一次系统设计作答。"""

    __tablename__ = "design_attempt"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)

    question_slug: str = Field(index=True)

    #: 文字说明。评估的主要依据——图能看，但方案讲得清才算想清楚。
    writeup: str = Field(default="", sa_column=Column(Text))
    #: Excalidraw 场景 JSON（原样存，前端能重新载入继续画）
    scene: str = Field(default="", sa_column=Column(Text))
    #: 画布里的文字标签，抽出来给模型看
    scene_labels: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    review: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    verdict: str = Field(default="", index=True)
    summary: str = Field(default="", sa_column=Column(Text))

    target_id: int | None = Field(default=None, foreign_key="job_target.id")
