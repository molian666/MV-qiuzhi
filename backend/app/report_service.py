"""报告编排：读库 → 调模型 → 规范化 → 落库。

与 `analysis/scoring.py` 的分工：那边是纯函数（可单测），这边只负责 I/O 编排。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete
from sqlmodel import Session, select

from .analysis.scoring import (
    build_exchanges,
    build_scoring_messages,
    normalize_report,
)
from .expression_service import attach_to_report
from .learning_service import MAX_TOPICS_PER_OBSERVATION, apply_observation, ensure_topic
from .models.core import JobTarget, Resume
from .models.scoring import ImprovementItem, ScoreReport
from .models.session import InterviewSession
from .providers.llm.base import LLMClient, LLMError, extract_json
from .repositories import ordered_turns
from .services import log_llm_error, log_llm_result


class ReportError(RuntimeError):
    """无法生成报告（没有回答等业务原因）。"""


@dataclass(slots=True)
class ReportBundle:
    report: ScoreReport
    items: list[ImprovementItem] = field(default_factory=list)
    target_label: str = ""
    reused: bool = False


def _target_label(target: JobTarget | None) -> str:
    if target is None:
        return ""
    return " · ".join(part for part in (target.company, target.title) if part)


def load_existing_report(db: Session, session_id: int) -> ReportBundle | None:
    rows = list(
        db.exec(
            select(ScoreReport)
            .where(ScoreReport.session_id == session_id)
            .order_by(ScoreReport.id)  # type: ignore[arg-type]
        ).all()
    )
    if not rows:
        return None

    report_row = rows[-1]
    items = list(
        db.exec(
            select(ImprovementItem)
            .where(ImprovementItem.report_id == report_row.id)
            .order_by(ImprovementItem.id)  # type: ignore[arg-type]
        ).all()
    )
    session_row = db.get(InterviewSession, session_id)
    target = None
    if session_row is not None and session_row.target_id:
        target = db.get(JobTarget, session_row.target_id)
    return ReportBundle(report=report_row, items=items, target_label=_target_label(target), reused=True)


async def generate_report(
    db: Session,
    *,
    session_id: int,
    llm: LLMClient,
    force: bool = True,
) -> ReportBundle:
    """给一场面试生成（或重新生成）报告。

    `force=False` 时若已有报告就直接复用——打开报告页不该每次都重新烧 token。
    `force=True` 会先清掉旧报告，避免同一场会话堆出多份互相矛盾的分数。
    """
    session_row = db.get(InterviewSession, session_id)
    if session_row is None:
        raise LookupError(f"会话不存在：{session_id}")

    if not force:
        existing = load_existing_report(db, session_id)
        if existing is not None:
            return existing

    turns = ordered_turns(db, session_id)
    valid_turn_ids = {turn.id for turn in turns if turn.id is not None}
    exchanges = build_exchanges(turns)

    if not any(exchange.answer.strip() for exchange in exchanges):
        raise ReportError("这场会话还没有任何回答，无法评分。先回答至少一问再生成报告。")

    target = db.get(JobTarget, session_row.target_id) if session_row.target_id else None
    resume = db.get(Resume, session_row.resume_id) if session_row.resume_id else None
    provider = getattr(llm, "name", "")

    messages = build_scoring_messages(
        target_label=_target_label(target),
        resume_text=resume.content_text if resume is not None else "",
        duration_ms=session_row.duration_ms,
        max_questions=int(session_row.plan.get("max_questions") or 0),
        exchanges=exchanges,
    )

    try:
        result = await llm.complete(task="scoring", messages=messages, temperature=0.2, json_object=True)
    except LLMError as exc:
        log_llm_error(db, task="scoring", provider=provider, error=str(exc), session_id=session_id)
        raise

    log_llm_result(db, result, session_id=session_id)

    try:
        raw = extract_json(result.text)
    except ValueError:
        # 模型没给合法 JSON：不报错，交给 normalize 兜底（默认分 + 可执行提示），
        # 但把原始输出留个痕迹，方便排查是哪次调用的问题。
        raw = {"summary": f"（模型输出无法解析为 JSON，已按默认分占位）原始片段：{result.text[:200]}"}

    normalized = normalize_report(raw, valid_turn_ids=valid_turn_ids)

    # 清旧报告（重新生成场景）
    old = load_existing_report(db, session_id)
    if old is not None:
        old_ids = [row.id for row in old.items if row.id is not None]
        if old_ids:
            db.exec(delete(ImprovementItem).where(ImprovementItem.report_id.in_(old_ids)))  # type: ignore[arg-type]
        db.exec(delete(ScoreReport).where(ScoreReport.session_id == session_id))
        db.commit()

    report_row = ScoreReport(
        session_id=session_id,
        overall=float(normalized.overall),
        dimension_scores=normalized.dimensions_payload(),
        summary=normalized.summary,
        model=result.model,
    )
    db.add(report_row)
    db.commit()
    db.refresh(report_row)

    # M9：把表达体检的分数并进 dimension_scores（加一个「表达」维度，不改表结构）
    attach_to_report(db, session_id=session_id, report_row=report_row)

    items: list[ImprovementItem] = []
    for item in normalized.items:
        row = ImprovementItem(
            report_id=report_row.id or 0,
            severity=item.severity,
            dimension=item.dimension,
            problem=item.problem,
            evidence_turn_ids=item.evidence_turn_ids,
            suggestion=item.suggestion,
            corrected_answer=item.corrected_answer,
            drill=item.drill,
            topics=item.topics,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        items.append(row)

    # 会话摘要同步一份：历史列表不用打开报告就能看到大概
    session_row.summary = normalized.summary[:2000]
    db.add(session_row)
    db.commit()

    # M5：把报告里的能力标签累积成掌握度，供"今日训练"挑题使用
    _accumulate_mastery(
        db,
        normalized=normalized,
        items=items,
        session_id=session_id,
        report_id=report_row.id or 0,
    )

    return ReportBundle(report=report_row, items=items, target_label=_target_label(target))


def _accumulate_mastery(db: Session, *, normalized, items, session_id: int, report_id: int) -> int:
    """把报告的能力标签累积成掌握度。

    用"该改进项所属维度"的得分作为这次观察的分数：维度分代表这件事做得怎么样，
    而改进项正好指向那个维度。`scores` 缺该项时按 3 分（中位）处理。
    """
    dimension_scores = {dimension.key: dimension.score for dimension in normalized.dimensions}
    observations = 0

    for row, item in zip(items, normalized.items):
        score = float(dimension_scores.get(item.dimension, 3))
        names = item.topics or [item.dimension_label]
        for name in names[:MAX_TOPICS_PER_OBSERVATION]:
            topic = ensure_topic(db, name)
            if topic is None or topic.id is None:
                continue
            apply_observation(
                db,
                topic_id=int(topic.id),
                score=score,
                source="report",
                session_id=session_id,
                report_id=report_id,
                item_id=row.id,
                note=f"报告：{item.dimension_label}",
            )
            observations += 1

    return observations
