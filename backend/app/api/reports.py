"""报告 API：生成/读取某场面试的评分报告。

约定：
- `POST` 是显式动作（用户点"生成/重新生成"）→ 默认 `force=true` 重新算；
- `GET` 只读已有报告，不存在就 404，让前端决定要不要触发生成。
  这样打开报告页不会每次都白烧一次模型调用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..analysis.rubric import dimension_keys, label_for
from ..deps import get_llm, get_session
from ..models.scoring import ScoreReport
from ..providers.llm.base import LLMClient
from ..report_service import ReportBundle, ReportError, generate_report, load_existing_report
from ..repositories import ordered_turns
from ..schemas.report import (
    DimensionScoreOut,
    EvidenceTurnOut,
    ImprovementItemOut,
    ReportSummaryOut,
    ScoreReportOut,
)
from ..services import turn_audio_url

router = APIRouter(tags=["reports"])

_DIMENSION_ORDER = {key: index for index, key in enumerate(dimension_keys())}


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    numbers: list[int] = []
    for item in value:
        try:
            numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return numbers


def _to_out(db: Session, bundle: ReportBundle) -> ScoreReportOut:
    report = bundle.report
    session_id = report.session_id

    raw_dimensions = report.dimension_scores or {}
    dimensions: list[DimensionScoreOut] = []
    for key, value in raw_dimensions.items():
        if not isinstance(value, dict):
            continue
        dimensions.append(
            DimensionScoreOut(
                key=key,
                label=str(value.get("label") or label_for(key)),
                score=int(value.get("score") or 0),
                comment=str(value.get("comment") or ""),
                evidence_turn_ids=_int_list(value.get("evidence_turn_ids")),
                # 老报告里没有这个键时按"已评分"处理：那时缺项是被填成 3 分的，
                # 当作已评分才符合当时的事实（现在的新报告会明确写 False）
                scored=bool(value.get("scored", True)),
            )
        )
    # 按 rubric 顺序排：雷达图的轴顺序必须稳定，否则同一份数据两次渲染看起来不一样
    dimensions.sort(key=lambda item: _DIMENSION_ORDER.get(item.key, 99))

    turns = ordered_turns(db, session_id) if session_id else []
    turns_by_id = {turn.id: turn for turn in turns if turn.id is not None}

    items: list[ImprovementItemOut] = []
    for row in bundle.items:
        evidence: list[EvidenceTurnOut] = []
        for turn_id in row.evidence_turn_ids or []:
            turn = turns_by_id.get(turn_id)
            if turn is None:
                continue
            evidence.append(
                EvidenceTurnOut(
                    turn_id=turn_id,
                    speaker=turn.speaker,
                    text=turn.text,
                    start_ms=turn.start_ms,
                    end_ms=turn.end_ms,
                    audio_url=turn_audio_url(session_id, turn.audio_path),
                )
            )
        items.append(
            ImprovementItemOut(
                id=row.id or 0,
                severity=row.severity,
                dimension=row.dimension,
                dimension_label=label_for(row.dimension),
                problem=row.problem,
                suggestion=row.suggestion,
                corrected_answer=row.corrected_answer,
                drill=row.drill,
                topics=list(row.topics or []),
                evidence=evidence,
            )
        )

    return ScoreReportOut(
        id=report.id or 0,
        session_id=session_id,
        overall=report.overall,
        summary=report.summary,
        model=report.model,
        generated_at=report.generated_at,
        target_label=bundle.target_label,
        reused=bundle.reused,
        dimensions=dimensions,
        items=items,
    )


@router.post("/api/sessions/{session_id}/report", response_model=ScoreReportOut, summary="生成（或重新生成）报告")
async def create_report(
    session_id: int,
    force: bool = Query(default=True, description="true=重新评分；false=已有报告就直接复用"),
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> ScoreReportOut:
    try:
        bundle = await generate_report(db, session_id=session_id, llm=llm, force=force)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(db, bundle)


@router.get("/api/sessions/{session_id}/report", response_model=ScoreReportOut, summary="读取已有报告")
def read_report(session_id: int, db: Session = Depends(get_session)) -> ScoreReportOut:
    bundle = load_existing_report(db, session_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="这场面试还没有报告（可 POST 生成）")
    return _to_out(db, bundle)


@router.get("/api/reports", response_model=list[ReportSummaryOut], summary="报告列表")
def list_reports(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[ReportSummaryOut]:
    statement = (
        select(ScoreReport)
        .order_by(ScoreReport.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    return [
        ReportSummaryOut(
            id=row.id or 0,
            session_id=row.session_id,
            overall=row.overall,
            generated_at=row.generated_at,
            summary=row.summary,
        )
        for row in db.exec(statement).all()
    ]
