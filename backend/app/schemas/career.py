"""导出与入职长期的请求/响应模型（M15）。"""

from __future__ import annotations

from datetime import date, datetime
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
    integrations: list[dict[str, Any]]


class OnboardingCheckIn(BaseModel):
    done: bool


class OnboardingTaskIn(BaseModel):
    category: str = "背调材料"
    title: str = ""
    why_needed: str = ""
    where_to_do: str = ""
    note: str = ""
    sort_order: int | None = None


class OnboardingTaskOut(BaseModel):
    id: int
    category: str
    title: str
    why_needed: str
    where_to_do: str
    note: str
    done: bool
    done_at: datetime | None = None
    sort_order: int

    model_config = {"from_attributes": True}


class CategoryProgressOut(BaseModel):
    category: str
    total: int
    done: int


class OnboardingProgressOut(BaseModel):
    total: int
    done: int
    remaining: int
    pct: int
    complete: bool
    by_category: list[CategoryProgressOut]


class OnboardingOut(BaseModel):
    tasks: list[OnboardingTaskOut]
    progress: OnboardingProgressOut


class SeedOut(BaseModel):
    added: int


class ObjectiveIn(BaseModel):
    title: str = ""
    period: str = ""
    note: str = ""
    status: str | None = None
    sort_order: int | None = None


class KeyResultIn(BaseModel):
    title: str = ""
    unit: str = ""
    target_value: float = 0.0
    current_value: float = 0.0
    confidence: int = Field(default=0, ge=0, le=100)
    sort_order: int | None = None


class KeyResultProgressIn(BaseModel):
    current_value: float
    confidence: int | None = Field(default=None, ge=0, le=100)


class KrViewOut(BaseModel):
    id: int
    title: str
    unit: str
    target_value: float
    current_value: float
    confidence: int
    sort_order: int
    pct: float
    status: str
    updated_at: datetime | None = None


class ObjectiveViewOut(BaseModel):
    id: int
    title: str
    period: str
    note: str
    status: str
    status_label: str
    sort_order: int
    pct: float
    kr_done: int
    krs: list[KrViewOut]


class ReviewDraftOut(BaseModel):
    title: str
    sections: list[dict[str, Any]]
    markdown: str
    note: str


class PlanItemIn(BaseModel):
    kind: str = "other"
    title: str = ""
    detail: str = ""
    #: 只要日期（"2027-06-30"），不要时刻——见 models/career.py 的说明
    target_date: date | None = None
    status: str | None = None


class PlanItemOut(BaseModel):
    id: int
    kind: str
    title: str
    detail: str
    target_date: date | None = None
    status: str
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class BenchmarkIn(BaseModel):
    company: str = ""
    title: str = ""
    city: str = ""
    range_low: float = 0.0
    range_high: float = 0.0
    currency: str = "CNY"
    period: str = ""
    source: str = ""
    source_url: str = ""
    verified: bool = False
    note: str = ""


class BenchmarkOut(BaseModel):
    id: int
    company: str
    title: str
    city: str
    range_low: float
    range_high: float
    currency: str
    period: str
    source: str
    source_url: str
    verified: bool
    note: str
    recorded_at: datetime | None = None

    model_config = {"from_attributes": True}


class BenchmarkSummaryOut(BaseModel):
    count: int = 0
    verified_count: int = 0
    min_low: float | None = None
    median_low: float | None = None
    max_low: float | None = None
    min_high: float | None = None
    median_high: float | None = None
    max_high: float | None = None
    median_range: list[float] | None = None
    sources: list[str] = []
    note: str = ""


class TimingItemOut(BaseModel):
    kind: str
    question: str
    why: str
    where: str


class TimingSignalOut(BaseModel):
    label: str
    items: list[str]


class TimingOut(BaseModel):
    promotion: list[TimingItemOut]
    switch: list[TimingItemOut]
    signals: list[TimingSignalOut]
    note: str


class CareerOptionsOut(BaseModel):
    categories: list[str]
    okr_statuses: list[dict[str, str]]
    plan_kinds: list[dict[str, str]]
    plan_statuses: list[dict[str, str]]
