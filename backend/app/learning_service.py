"""标签树与掌握度：把报告里的能力标签累积成可用的学习状态。

这一层是 M5 的核心存储逻辑：
- `ensure_topic` 把模型给的自由文本标签（"量化结果" / "结果导向"）收敛成稳定的标签行；
- `apply_observation` 做一次"观察 → 掌握度更新 + 事件留痕 + SM-2 排程"的完整动作，
  报告与训练两条路径共用它，避免两处各写一套更新规则。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .analysis.mastery import is_high_risk, update_mastery
from .analysis.review import DEFAULT_EASE, FIRST_INTERVAL, next_schedule, quality_from_score
from .clock import utcnow
from .models.scoring import MasteryEvent, ReviewTask, SkillMastery, SkillTopic

MAX_TOPIC_LENGTH = 40
MAX_TOPICS_PER_OBSERVATION = 6


def normalize_topic(name: str) -> str:
    """标签规范化：去首尾空白、压缩内部空白、截断过长标签。"""
    cleaned = " ".join((name or "").split())
    return cleaned[:MAX_TOPIC_LENGTH]


def ensure_topic(db: Session, name: str, *, category: str = "skill") -> SkillTopic | None:
    """按名字取或建标签（同名只建一次）。空名字返回 None。"""
    label = normalize_topic(name)
    if not label:
        return None

    existing = db.exec(select(SkillTopic).where(SkillTopic.name == label)).first()
    if existing is not None:
        return existing

    topic = SkillTopic(name=label, category=category)
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def ensure_mastery(db: Session, topic_id: int) -> SkillMastery:
    row = db.exec(select(SkillMastery).where(SkillMastery.topic_id == topic_id)).first()
    if row is not None:
        return row
    row = SkillMastery(topic_id=topic_id, mastery=0.0, attempts=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def recent_scores(db: Session, topic_id: int, *, limit: int = 5) -> list[float]:
    """最近的观察分数（新的在后）——用于高危判定。"""
    rows = db.exec(
        select(MasteryEvent)
        .where(MasteryEvent.topic_id == topic_id)
        .order_by(MasteryEvent.id.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [float(row.score) for row in reversed(rows)]


def apply_observation(
    db: Session,
    *,
    topic_id: int,
    score: float,
    source: str,
    session_id: int | None = None,
    report_id: int | None = None,
    item_id: int | None = None,
    note: str = "",
) -> tuple[SkillMastery, MasteryEvent]:
    """一次观察：更新掌握度、留事件、排下一次复习。"""
    mastery_row = ensure_mastery(db, topic_id)
    before = float(mastery_row.mastery)
    # attempts == 0 表示这个标签第一次被观察到：交给 update_mastery 用"直接取目标值"的语义
    after = update_mastery(None if mastery_row.attempts == 0 else before, score)

    mastery_row.mastery = after
    mastery_row.attempts += 1
    if score >= 4:
        mastery_row.correct_hits += 1
    mastery_row.updated_at = utcnow()
    mastery_row.last_seen_at = mastery_row.updated_at
    db.add(mastery_row)

    event = MasteryEvent(
        topic_id=topic_id,
        mastery_before=before,
        mastery_after=after,
        score=float(score),
        source=source,
        session_id=session_id,
        report_id=report_id,
        item_id=item_id,
        note=note[:500],
    )
    db.add(event)
    db.commit()
    db.refresh(mastery_row)
    db.refresh(event)

    _reschedule(db, topic_id=topic_id, score=score)
    return mastery_row, event


def _reschedule(db: Session, *, topic_id: int, score: float) -> ReviewTask:
    """按 SM-2 更新这个标签的下次复习时间。"""
    task = db.exec(
        select(ReviewTask).where(ReviewTask.topic_id == topic_id, ReviewTask.done_at.is_(None))  # type: ignore[union-attr]
    ).first()

    quality = quality_from_score(score)
    if task is None:
        schedule = next_schedule(quality=quality)
        task = ReviewTask(
            topic_id=topic_id,
            due_at=schedule.due_at,
            interval_days=schedule.interval_days,
            ease=schedule.ease,
            repetitions=schedule.repetitions,
            updated_at=utcnow(),
        )
    else:
        schedule = next_schedule(
            quality=quality,
            interval_days=task.interval_days or FIRST_INTERVAL,
            ease=task.ease or DEFAULT_EASE,
            repetitions=task.repetitions,
        )
        task.due_at = schedule.due_at
        task.interval_days = schedule.interval_days
        task.ease = schedule.ease
        task.repetitions = schedule.repetitions
        task.updated_at = utcnow()

    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def due_topic_ids(db: Session, *, now=None) -> set[int]:
    """已经到期待复习的标签。"""
    moment = now or utcnow()
    rows = db.exec(
        select(ReviewTask).where(ReviewTask.done_at.is_(None), ReviewTask.due_at <= moment)
    ).all()
    return {int(row.topic_id) for row in rows if row.topic_id is not None}


def mastery_overview(db: Session) -> list[dict[str, object]]:
    """掌握度清单（含高危标记），按掌握度升序——最该练的排最前。"""
    topics = {int(topic.id): topic for topic in db.exec(select(SkillTopic)).all() if topic.id is not None}
    rows = db.exec(select(SkillMastery)).all()

    items: list[dict[str, object]] = []
    for row in rows:
        topic = topics.get(int(row.topic_id))
        if topic is None:
            continue
        scores = recent_scores(db, int(row.topic_id))
        items.append(
            {
                "topic_id": int(row.topic_id),
                "name": topic.name,
                "category": topic.category,
                "mastery": float(row.mastery),
                "attempts": int(row.attempts),
                "correct_hits": int(row.correct_hits),
                "updated_at": row.updated_at,
                "high_risk": is_high_risk(mastery=float(row.mastery), recent_scores=scores),
            }
        )

    items.sort(key=lambda item: (float(item["mastery"]), str(item["name"])))
    return items


def topic_history(db: Session, topic_id: int, *, limit: int = 60) -> list[MasteryEvent]:
    rows = db.exec(
        select(MasteryEvent)
        .where(MasteryEvent.topic_id == topic_id)
        .order_by(MasteryEvent.id.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return list(reversed(rows))
