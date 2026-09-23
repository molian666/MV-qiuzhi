"""Offer 与决策服务：Offer CRUD、矩阵计算、谈判编排、风险与合同扫描。

谈判复用 `interview_session`（`mode="negotiation"`）而不是新建一张表：
一场谈判就是"一场对话 + 一份结论文档"，和面试的结构完全一样，
历史、逐轮记录、导出这些能力都能直接复用。
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlmodel import Session, select

from .analysis.company_risk import CompanyRiskReport, scan_text
from .analysis.contract_check import ContractReport, build_report
from .analysis.negotiation import (
    BriefTurn,
    NegotiationBrief,
    build_brief,
    build_opening_messages,
    build_reply_messages,
)
from .analysis.offer_matrix import MatrixResult, annual_comp, build_matrix, offers_from_rows
from .clock import utcnow
from .models.offer import Offer
from .models.session import InterviewSession, SessionTurn
from .providers.llm.base import LLMClient, LLMError
from .repositories import ordered_turns
from .services import log_llm_error, log_llm_result

NEGOTIATION_MODE = "negotiation"


# --------------------------------------------------------------------------- #
# Offer
# --------------------------------------------------------------------------- #
def list_offers(db: Session) -> list[Offer]:
    return list(db.exec(select(Offer).order_by(Offer.id.desc())).all())  # type: ignore[arg-type]


def total_comp_of(offer: Offer) -> float:
    return annual_comp(
        monthly_salary=offer.monthly_salary,
        months_per_year=offer.months_per_year,
        annual_bonus=offer.annual_bonus,
        equity_per_year=offer.equity_per_year,
        signing_bonus=offer.signing_bonus,
    )


def create_offer(db: Session, *, payload: dict[str, Any]) -> Offer:
    offer = Offer(**payload)
    offer.updated_at = utcnow()
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


def update_offer(db: Session, offer_id: int, *, payload: dict[str, Any]) -> Offer | None:
    offer = db.get(Offer, offer_id)
    if offer is None:
        return None
    for key, value in payload.items():
        if hasattr(offer, key):
            setattr(offer, key, value)
    offer.updated_at = utcnow()
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


def delete_offer(db: Session, offer_id: int) -> bool:
    offer = db.get(Offer, offer_id)
    if offer is None:
        return False
    db.delete(offer)
    db.commit()
    return True


def matrix(db: Session, *, weights: dict[str, float] | None = None) -> MatrixResult:
    return build_matrix(offers_from_rows(list_offers(db)), weights)


# --------------------------------------------------------------------------- #
# 风险与合同（纯转发，逻辑都在 analysis 层）
# --------------------------------------------------------------------------- #
def risk(text: str) -> CompanyRiskReport:
    return scan_text(text)


def contract(text: str) -> ContractReport:
    return build_report(text)


# --------------------------------------------------------------------------- #
# 谈判
# --------------------------------------------------------------------------- #
def _plan_for(*, bottom_line: float, target: float, ceiling: float | None) -> dict[str, Any]:
    """把"底线/目标"翻译成 HR 的行为参数。

    - 预算上限留空时按目标上浮推算（不知道对方预算时也得有个数才能演）；
    - 开场报价取上限的 82%：真实 HR 不会一上来就给到顶；
    - 每轮让步 = 剩余空间的 1/4，也就是大概 4 轮让到位。
    """
    floor = max(0.0, float(bottom_line or 0))
    goal = max(0.0, float(target or 0))
    top = float(ceiling or 0) or max(goal * 1.15, floor * 1.2, 400_000)
    opening = round(max(floor * 0.95, top * 0.82), -3)
    step = round(max(5_000.0, (top - opening) / 4), -3)
    return {
        "bottom_line": floor,
        "target": goal,
        "ceiling": round(top, -3),
        "opening": opening,
        "step": step,
    }


def negotiation_plan(session: InterviewSession) -> dict[str, Any]:
    plan = dict(session.plan or {})
    return {
        "company": str(plan.get("company") or ""),
        "title": str(plan.get("title") or ""),
        "bottom_line": float(plan.get("bottom_line") or 0),
        "target": float(plan.get("target") or 0),
        "ceiling": float(plan.get("ceiling") or 0),
        "opening": float(plan.get("opening") or 0),
        "step": float(plan.get("step") or 0),
    }


def list_negotiations(db: Session, *, limit: int = 30) -> list[InterviewSession]:
    statement = (
        select(InterviewSession)
        .where(InterviewSession.mode == NEGOTIATION_MODE)
        .order_by(InterviewSession.id.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    return list(db.exec(statement).all())


async def start_negotiation(
    db: Session,
    *,
    llm: LLMClient,
    company: str = "",
    title: str = "",
    bottom_line: float = 0,
    target: float = 0,
    ceiling: float | None = None,
    script: str = "",
) -> InterviewSession:
    plan = {
        "company": company,
        "title": title,
        **_plan_for(bottom_line=bottom_line, target=target, ceiling=ceiling),
    }
    session = InterviewSession(
        mode=NEGOTIATION_MODE,
        status="live",
        provider=getattr(llm, "name", ""),
        plan=plan,
        started_at=utcnow(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    session_id = int(session.id or 0)

    messages = build_opening_messages(company=company, title=title, script=script)
    try:
        result = await llm.complete(task="negotiation", messages=messages, temperature=0.7)
    except LLMError as exc:
        log_llm_error(db, task="negotiation", provider=getattr(llm, "name", ""), error=str(exc), session_id=session_id)
        raise

    log_llm_result(db, result, session_id=session_id)
    db.add(
        SessionTurn(
            session_id=session_id,
            idx=0,
            speaker="interviewer",
            text=result.text.strip(),
            kind="opening",
            meta={"negotiation": True},
        )
    )
    db.commit()
    return session


async def reply(db: Session, *, llm: LLMClient, session_id: int, text: str) -> InterviewSession:
    session = db.get(InterviewSession, session_id)
    if session is None:
        raise LookupError(f"谈判不存在：{session_id}")

    turns = ordered_turns(db, session_id)
    db.add(
        SessionTurn(
            session_id=session_id,
            idx=len(turns),
            speaker="candidate",
            text=text.strip(),
            kind="answer",
            meta={"negotiation": True},
        )
    )
    db.commit()

    turns = ordered_turns(db, session_id)
    plan = negotiation_plan(session)
    messages = build_reply_messages(
        candidate_turns=[turn.text for turn in turns if turn.speaker == "candidate"],
        history=[{"speaker": turn.speaker, "text": turn.text} for turn in turns],
        ceiling=plan["ceiling"],
        opening=plan["opening"],
        step=plan["step"],
    )

    try:
        result = await llm.complete(task="negotiation", messages=messages, temperature=0.8)
    except LLMError as exc:
        log_llm_error(db, task="negotiation", provider=getattr(llm, "name", ""), error=str(exc), session_id=session_id)
        raise

    log_llm_result(db, result, session_id=session_id)
    db.add(
        SessionTurn(
            session_id=session_id,
            idx=len(turns),
            speaker="interviewer",
            text=result.text.strip(),
            kind="reply",
            meta={"negotiation": True},
        )
    )
    db.commit()
    return session


def brief(db: Session, session: InterviewSession) -> NegotiationBrief:
    plan = negotiation_plan(session)
    turns: Sequence[SessionTurn] = ordered_turns(db, session.id or 0)
    return build_brief(
        [BriefTurn(speaker=turn.speaker, text=turn.text) for turn in turns],
        bottom_line=plan["bottom_line"],
        target=plan["target"],
    )


def negotiation_turns(db: Session, session_id: int) -> list[SessionTurn]:
    return ordered_turns(db, session_id)
