"""投递数据的对外模型（M6）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ImportIn(BaseModel):
    text: str = Field(min_length=1, description="CSV/TSV 原文（第一行是表头）")
    channel: str = Field(default="other", description="文本里没有渠道列时的默认渠道")
    default_year: int | None = Field(default=None, description="日期缺年份时用哪一年")


class IngestIn(BaseModel):
    """浏览器扩展推来的结构：平台名 + 若干"表头→值"的行。"""

    platform: str = ""
    rows: list[dict[str, Any]] = Field(min_length=1)


class ImportOutcomeOut(BaseModel):
    batch_id: int
    source: str
    rows: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    matched_columns: dict[str, str] = Field(default_factory=dict)
    unmatched_headers: list[str] = Field(default_factory=list)
    skipped_details: list[tuple[int, str]] = Field(default_factory=list)
    note: str = ""


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    title: str
    channel: str
    status: str
    applied_at: datetime
    salary_range: str = ""
    city: str = ""
    resume_version: str = ""
    jd_url: str = ""
    note: str = ""
    source: str = "manual"
    created_at: datetime


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1)
    title: str = ""
    channel: str = "other"
    status: str = "applied"
    applied_at: datetime | None = None
    salary_range: str = ""
    city: str = ""
    resume_version: str = ""
    jd_url: str = ""
    note: str = ""


class StatusChangeIn(BaseModel):
    to_status: str
    raw_ref: str = "手动"


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str = ""
    source: str = "csv"
    at: datetime
    rows: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    note: str = ""
