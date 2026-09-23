"""半自动投递的对外模型（M10）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApplySettingsIn(BaseModel):
    enabled: bool = False
    risk_acknowledged: bool = False
    platform: str = "mock"
    default_channel: str = "boss"
    want_salary_min: int = 0
    per_minute: int = 3
    per_day: int = 30
    jitter_min_seconds: float = 3.0
    jitter_max_seconds: float = 9.0
    selectors: dict[str, str] = Field(default_factory=dict)
    user_data_dir: str = ""


class ApplySettingsOut(BaseModel):
    enabled: bool
    risk_acknowledged: bool
    platform: str
    default_channel: str
    want_salary_min: int
    per_minute: int
    per_day: int
    jitter_min_seconds: float
    jitter_max_seconds: float
    selectors: dict[str, str] = Field(default_factory=dict)
    user_data_dir: str = ""
    #: 硬上限（配置会被夹到这个范围内，避免一键把自己投废）
    max_per_minute: int = 10
    max_per_day: int = 60


class ApplyRuleIn(BaseModel):
    name: str = ""
    kind: str = "keyword"
    enabled: bool = True
    priority: int = 100
    params: dict[str, Any] = Field(default_factory=dict)


class ApplyRuleOut(ApplyRuleIn):
    id: int


class ApplySearchIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    city: str = ""
    salary_min: int = 0
    limit: int = Field(default=20, ge=1, le=60)
    fetch_detail: bool = False
    resume_id: int | None = None


class CandidateOut(BaseModel):
    job_posting_id: int
    external_id: str
    company: str
    title: str
    salary_range: str = ""
    city: str = ""
    jd_url: str = ""
    matched_score: float = 0.0
    passed: bool = True
    blocked_by: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    say_hi: str = ""
    task_id: int | None = None


class ApplySearchOut(BaseModel):
    fetched: int = 0
    passed: int = 0
    blocked: int = 0
    queued: int = 0
    duplicates: int = 0
    candidates: list[CandidateOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ApproveIn(BaseModel):
    task_ids: list[int] = Field(min_length=1)
    say_hi_overrides: dict[int, str] = Field(default_factory=dict)


class SkipIn(BaseModel):
    task_ids: list[int] = Field(min_length=1)


class ExecuteIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=30)
    dry_run: bool = False


class QueueTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    company: str = ""
    title: str = ""
    salary_range: str = ""
    city: str = ""
    jd_url: str = ""
    matched_score: float | None = None
    say_hi_text: str = ""
    attempts: int = 0
    error: str = ""
    created_at: datetime
    approved_at: datetime | None = None
    sent_at: datetime | None = None


class QueueSnapshotOut(BaseModel):
    total: int = 0
    pending: int = 0
    approved: int = 0
    sent: int = 0
    failed: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)


class ExecutionItemOut(BaseModel):
    task_id: int
    company: str = ""
    title: str = ""
    ok: bool = False
    detail: str = ""


class ExecutionOutcomeOut(BaseModel):
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    stopped_reason: str = ""
    tripped: bool = False
    breaker_message: str = ""
    items: list[ExecutionItemOut] = Field(default_factory=list)


class ApplyStatusOut(BaseModel):
    enabled: bool = False
    risk_acknowledged: bool = False
    platform: str = "mock"
    circuit_broken_until: datetime | None = None
    broken: bool = False
    daily_sent: int = 0
    daily_limit: int = 30
    remaining_quota: int = 0
    adapter_ready: bool = True
    adapter_note: str = ""
