"""Offer 与决策的对外模型（M12）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Offer
# --------------------------------------------------------------------------- #
class OfferIn(BaseModel):
    company: str = ""
    title: str = ""
    application_id: int | None = None

    monthly_salary: float = 0
    months_per_year: float = 12
    annual_bonus: float = 0
    equity_per_year: float = 0
    signing_bonus: float = 0

    #: 各维度 0–10 分（key 见 /api/decision/options 返回的 dimensions）
    factors: dict[str, float] = Field(default_factory=dict)

    status: str = "pending"
    deadline: datetime | None = None
    note: str = ""


class OfferOut(OfferIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    #: 首年总包（服务端算出来，前端不用自己算）
    total_comp: float = 0.0
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# 矩阵
# --------------------------------------------------------------------------- #
class WeightsIn(BaseModel):
    weights: dict[str, float] = Field(default_factory=dict)


class ScoreRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    offer_id: int
    company: str
    title: str = ""
    total: float = 0.0
    rank: int = 0
    breakdown: dict[str, float] = Field(default_factory=dict)
    stars: dict[str, float] = Field(default_factory=dict)


class FlipPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    factor: str
    label: str = ""
    weight: float
    winner: int
    winner_company: str
    loser: int
    loser_company: str
    message: str = ""


class MatrixOut(BaseModel):
    rows: list[ScoreRowOut] = Field(default_factory=list)
    flips: list[FlipPointOut] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = ""


# --------------------------------------------------------------------------- #
# 风险与合同
# --------------------------------------------------------------------------- #
class SourceOut(BaseModel):
    label: str
    url: str
    what: str
    verified: bool = False


class RiskSignalOut(BaseModel):
    key: str
    label: str
    severity: str
    why: str
    action: str
    sources: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)
    disclaimer: str = ""


class RiskReportOut(BaseModel):
    signals: list[RiskSignalOut] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)
    disclaimer: str = ""
    checked_chars: int = 0


class ContractClauseOut(BaseModel):
    key: str
    label: str
    severity: str
    question: str
    why: str
    trap: str


class ClauseHitOut(BaseModel):
    clause: ContractClauseOut
    matched: list[str] = Field(default_factory=list)


class ContractReportOut(BaseModel):
    mentioned: list[ClauseHitOut] = Field(default_factory=list)
    checklist: list[ClauseHitOut] = Field(default_factory=list)
    missing: list[ClauseHitOut] = Field(default_factory=list)
    disclaimer: str = ""
    checked_chars: int = 0


class TextScanIn(BaseModel):
    text: str = ""


# --------------------------------------------------------------------------- #
# 谈判
# --------------------------------------------------------------------------- #
class NegotiationStartIn(BaseModel):
    company: str = ""
    title: str = ""
    #: 你的底线（低于这个数就不接受）
    bottom_line: float = 0
    #: 你的目标
    target: float = 0
    #: 对方预算上限（不知道就留空，服务端按目标推算）
    ceiling: float | None = None
    script: str = ""


class NegotiationReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class BriefOut(BaseModel):
    rounds: int = 0
    bottom_line: float = 0
    target: float = 0
    candidate_quotes: list[float] = Field(default_factory=list)
    interviewer_quotes: list[float] = Field(default_factory=list)
    first_ask: float | None = None
    last_ask: float | None = None
    final_offer: float | None = None
    bottom_line_held: bool | None = None
    concession_ratio: float = 0.0
    gap_to_target: float | None = None
    tactics_used: list[str] = Field(default_factory=list)
    tactics_missed: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    score: float = 0.0
    summary: str = ""
    caveat: str = ""


class NegotiationTurnOut(BaseModel):
    speaker: str
    text: str
    kind: str = ""
    created_at: datetime | None = None


class NegotiationOut(BaseModel):
    id: int
    company: str = ""
    title: str = ""
    status: str = ""
    plan: dict[str, Any] = Field(default_factory=dict)
    turns: list[NegotiationTurnOut] = Field(default_factory=list)
    brief: BriefOut | None = None


class DecisionOptionsOut(BaseModel):
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    tactics: list[dict[str, Any]] = Field(default_factory=list)
    risk_signals: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)
    contract_clauses: list[ContractClauseOut] = Field(default_factory=list)
    offer_statuses: list[dict[str, str]] = Field(default_factory=list)
