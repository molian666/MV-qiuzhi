"""Offer 与决策 API（M12）：Offer 矩阵、风险与合同扫描、薪资谈判。

路由不带统一前缀，因为它跨四个资源（/api/offers、/api/negotiations、
/api/decision/*），共用同一套"从数据算出结论"的逻辑。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..analysis.company_risk import signal_catalog, source_options
from ..analysis.contract_check import clause_options
from ..analysis.negotiation import tactic_options
from ..analysis.offer_matrix import DIMENSIONS, dimension_options, format_comp
from ..deps import get_llm, get_session
from ..models.offer import OFFER_STATUS_LABELS, OFFER_STATUSES, Offer
from ..models.session import InterviewSession
from ..offer_service import (
    brief as build_brief_for,
    contract,
    create_offer,
    delete_offer,
    list_negotiations,
    list_offers,
    matrix as build_matrix,
    negotiation_plan,
    negotiation_turns,
    reply as negotiation_reply,
    risk,
    start_negotiation,
    total_comp_of,
    update_offer,
)
from ..providers.llm.base import LLMClient, LLMError
from ..schemas.decision import (
    BriefOut,
    ClauseHitOut,
    ContractClauseOut,
    ContractReportOut,
    DecisionOptionsOut,
    FlipPointOut,
    MatrixOut,
    NegotiationOut,
    NegotiationReplyIn,
    NegotiationStartIn,
    NegotiationTurnOut,
    OfferIn,
    OfferOut,
    RiskReportOut,
    RiskSignalOut,
    ScoreRowOut,
    SourceOut,
    TextScanIn,
    WeightsIn,
)

router = APIRouter(tags=["decision"])


def _offer_out(offer: Offer) -> OfferOut:
    payload = dict(offer.model_dump())
    payload["total_comp"] = total_comp_of(offer)
    return OfferOut(**payload)


def _brief_out(brief) -> BriefOut:  # noqa: ANN001 - analysis 层的纯数据对象
    return BriefOut(**asdict(brief))


def _negotiation_out(db: Session, session: InterviewSession) -> NegotiationOut:
    plan = negotiation_plan(session)
    turns = negotiation_turns(db, session.id or 0)
    return NegotiationOut(
        id=int(session.id or 0),
        company=plan["company"],
        title=plan["title"],
        status=session.status,
        plan={**plan, "ceiling_text": format_comp(plan["ceiling"])},
        turns=[
            NegotiationTurnOut(speaker=turn.speaker, text=turn.text, kind=turn.kind, created_at=turn.created_at)
            for turn in turns
        ],
        brief=_brief_out(build_brief_for(db, session)),
    )


# --------------------------------------------------------------------------- #
# 选项
# --------------------------------------------------------------------------- #
@router.get("/api/decision/options", response_model=DecisionOptionsOut, summary="决策相关选项（维度/战术/信号/条款）")
def read_options() -> DecisionOptionsOut:
    return DecisionOptionsOut(
        dimensions=dimension_options(),
        tactics=tactic_options(),
        risk_signals=signal_catalog(),
        sources=[SourceOut(**item) for item in source_options()],
        contract_clauses=[ContractClauseOut(**item) for item in clause_options()],
        offer_statuses=[{"key": key, "label": OFFER_STATUS_LABELS[key]} for key in OFFER_STATUSES],
    )


# --------------------------------------------------------------------------- #
# Offer
# --------------------------------------------------------------------------- #
@router.get("/api/offers", response_model=list[OfferOut], summary="Offer 列表")
def read_offers(db: Session = Depends(get_session)) -> list[OfferOut]:
    return [_offer_out(offer) for offer in list_offers(db)]


@router.post("/api/offers", response_model=OfferOut, status_code=201, summary="登记一个 offer")
def create(payload: OfferIn, db: Session = Depends(get_session)) -> OfferOut:
    offer = create_offer(db, payload=payload.model_dump())
    return _offer_out(offer)


@router.put("/api/offers/{offer_id}", response_model=OfferOut, summary="修改 offer")
def update(offer_id: int, payload: OfferIn, db: Session = Depends(get_session)) -> OfferOut:
    offer = update_offer(db, offer_id, payload=payload.model_dump())
    if offer is None:
        raise HTTPException(status_code=404, detail=f"Offer 不存在：{offer_id}")
    return _offer_out(offer)


@router.delete("/api/offers/{offer_id}", status_code=204, summary="删除 offer")
def remove(offer_id: int, db: Session = Depends(get_session)) -> None:
    if not delete_offer(db, offer_id):
        raise HTTPException(status_code=404, detail=f"Offer 不存在：{offer_id}")


@router.post("/api/offers/matrix", response_model=MatrixOut, summary="对比矩阵 + 权重翻转点")
def read_matrix(payload: WeightsIn, db: Session = Depends(get_session)) -> MatrixOut:
    result = build_matrix(db, weights=payload.weights or None)
    return MatrixOut(
        rows=[ScoreRowOut(**asdict(row)) for row in result.rows],
        flips=[
            FlipPointOut(**asdict(flip), label=DIMENSIONS.get(flip.factor, flip.factor)) for flip in result.flips
        ],
        weights=result.weights,
        notes=result.notes,
        disclaimer=result.disclaimer,
    )


# --------------------------------------------------------------------------- #
# 风险与合同
# --------------------------------------------------------------------------- #
@router.post("/api/decision/risk", response_model=RiskReportOut, summary="公司风险信号扫描（只给来源，不下结论）")
def scan_risk(payload: TextScanIn) -> RiskReportOut:
    report = risk(payload.text)
    return RiskReportOut(
        signals=[RiskSignalOut(**asdict(signal)) for signal in report.signals],
        sources=[SourceOut(**asdict(source)) for source in report.sources],
        disclaimer=report.disclaimer,
        checked_chars=report.checked_chars,
    )


@router.post("/api/decision/contract", response_model=ContractReportOut, summary="合同与竞业提醒（非法律意见）")
def scan_contract(payload: TextScanIn) -> ContractReportOut:
    report = contract(payload.text)

    def _hit(item) -> ClauseHitOut:  # noqa: ANN001
        return ClauseHitOut(clause=ContractClauseOut(**asdict(item.clause)), matched=list(item.matched))

    return ContractReportOut(
        mentioned=[_hit(item) for item in report.mentioned],
        checklist=[_hit(item) for item in report.checklist],
        missing=[_hit(item) for item in report.missing],
        disclaimer=report.disclaimer,
        checked_chars=report.checked_chars,
    )


# --------------------------------------------------------------------------- #
# 谈判
# --------------------------------------------------------------------------- #
@router.get("/api/negotiations", response_model=list[NegotiationOut], summary="谈判历史")
def read_negotiations(db: Session = Depends(get_session)) -> list[NegotiationOut]:
    return [_negotiation_out(db, session) for session in list_negotiations(db)]


@router.post("/api/negotiations", response_model=NegotiationOut, status_code=201, summary="开始一场薪资谈判")
async def create_negotiation(
    payload: NegotiationStartIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> NegotiationOut:
    try:
        session = await start_negotiation(
            db,
            llm=llm,
            company=payload.company,
            title=payload.title,
            bottom_line=payload.bottom_line,
            target=payload.target,
            ceiling=payload.ceiling,
            script=payload.script,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"生成开场白失败：{exc}") from exc

    return _negotiation_out(db, session)


@router.get("/api/negotiations/{session_id}", response_model=NegotiationOut, summary="谈判详情（含简报）")
def read_negotiation(session_id: int, db: Session = Depends(get_session)) -> NegotiationOut:
    session = db.get(InterviewSession, session_id)
    if session is None or session.mode != "negotiation":
        raise HTTPException(status_code=404, detail=f"谈判不存在：{session_id}")
    return _negotiation_out(db, session)


@router.post("/api/negotiations/{session_id}/reply", response_model=NegotiationOut, summary="说一句，HR 回一句")
async def post_reply(
    session_id: int,
    payload: NegotiationReplyIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> NegotiationOut:
    try:
        session = await negotiation_reply(db, llm=llm, session_id=session_id, text=payload.text)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"生成回复失败：{exc}") from exc
    return _negotiation_out(db, session)
