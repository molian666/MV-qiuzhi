"""人脉与学习计划服务。

两条数据的来源不一样：
- **人脉**是用户手填的（谁、什么关系、聊过什么）；
- **学习清单是算出来的**：薄弱点来自 M5 的掌握度表，JD 缺口来自 M8 的匹配诊断
  （把投递记录里的 JD 逐条比对，统计"你缺哪个词、缺在多少份 JD 里"）。

这样"该学什么"就不是拍脑袋，而是"你确实弱 + 岗位确实要"的交集。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlmodel import Session, select

from .analysis.learning_path import (
    GapInput,
    LearningSuggestion,
    WeaknessInput,
    build_path,
    summarize_path,
)
from .analysis.networking import (
    ContactSnapshot,
    FollowUp,
    InteractionSnapshot,
    build_follow_ups,
    greeting_for,
    next_follow_up,
    relationship_health,
    timeline,
)
from .analysis.resume_match import match
from .analysis.skill_dict import skill_group
from .clock import utcnow
from .models.apply import Application
from .models.core import Resume
from .models.network import Contact, ContactInteraction, LearningItem
from .models.scoring import SkillMastery, SkillTopic


# --------------------------------------------------------------------------- #
# 人脉
# --------------------------------------------------------------------------- #
def list_contacts(db: Session, *, relation: str | None = None) -> list[Contact]:
    statement = select(Contact)
    if relation:
        statement = statement.where(Contact.relation == relation)
    return list(db.exec(statement.order_by(Contact.id.desc())).all())  # type: ignore[arg-type]


def create_contact(db: Session, *, payload: dict[str, Any]) -> Contact:
    contact = Contact(**payload)
    contact.updated_at = utcnow()
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact_id: int, *, payload: dict[str, Any]) -> Contact | None:
    contact = db.get(Contact, contact_id)
    if contact is None:
        return None
    for key, value in payload.items():
        if hasattr(contact, key):
            setattr(contact, key, value)
    contact.updated_at = utcnow()
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact_id: int) -> bool:
    contact = db.get(Contact, contact_id)
    if contact is None:
        return False
    # 先删子表：SQLite 开着外键约束，直接删父表会报错（这个坑在 M6 踩过）
    for row in db.exec(select(ContactInteraction).where(ContactInteraction.contact_id == contact_id)).all():
        db.delete(row)
    db.delete(contact)
    db.commit()
    return True


def add_interaction(db: Session, contact_id: int, *, payload: dict[str, Any]) -> ContactInteraction | None:
    contact = db.get(Contact, contact_id)
    if contact is None:
        return None

    row = ContactInteraction(contact_id=contact_id, **payload)
    if row.at is None:
        row.at = utcnow()
    db.add(row)

    # 同步便捷字段：列表页不用每行都去 join 时间线
    if contact.last_contact_at is None or row.at >= contact.last_contact_at:
        contact.last_contact_at = row.at
    contact.updated_at = utcnow()
    db.add(contact)

    db.commit()
    db.refresh(row)
    return row


def delete_interaction(db: Session, interaction_id: int) -> bool:
    row = db.get(ContactInteraction, interaction_id)
    if row is None:
        return False
    contact_id = row.contact_id
    db.delete(row)
    db.commit()

    # 重算 last_contact_at（删掉的可能是最新那条）
    contact = db.get(Contact, contact_id)
    if contact is not None:
        remaining = list(
            db.exec(select(ContactInteraction).where(ContactInteraction.contact_id == contact_id)).all()
        )
        contact.last_contact_at = max((item.at for item in remaining), default=None)
        db.add(contact)
        db.commit()
    return True


def _snapshot(db: Session, contact: Contact, *, interactions: Sequence[ContactInteraction] | None = None) -> ContactSnapshot:
    rows = list(interactions) if interactions is not None else list(
        db.exec(
            select(ContactInteraction)
            .where(ContactInteraction.contact_id == contact.id)
            .order_by(ContactInteraction.at)  # type: ignore[arg-type]
        ).all()
    )
    return ContactSnapshot(
        id=int(contact.id or 0),
        name=contact.name,
        relation=contact.relation,
        company=contact.company,
        title=contact.title,
        channel=contact.channel,
        note=contact.note,
        follow_up_days=int(contact.follow_up_days or 0),
        last_contact_at=contact.last_contact_at,
        key_dates=list(contact.key_dates or []),
        created_at=contact.created_at,
        interactions=[
            InteractionSnapshot(
                at=row.at,
                direction=row.direction,
                kind=row.kind,
                summary=row.summary,
                promised=row.promised,
                next_follow_up_at=row.next_follow_up_at,
            )
            for row in rows
        ],
    )


def contact_detail(db: Session, contact_id: int, *, now: datetime | None = None) -> dict[str, Any] | None:
    contact = db.get(Contact, contact_id)
    if contact is None:
        return None

    moment = now or utcnow()
    snapshot = _snapshot(db, contact)
    due = next_follow_up(snapshot, now=moment)
    return {
        "contact": contact,
        "timeline": timeline(snapshot.interactions),
        "health": relationship_health(snapshot, now=moment),
        "next_follow_up_at": due,
        "greeting": greeting_for(snapshot, now=moment),
    }


def follow_ups(
    db: Session,
    *,
    now: datetime | None = None,
    horizon_days: int = 14,
    include_later: bool = False,
) -> list[FollowUp]:
    moment = now or utcnow()
    contacts = list_contacts(db)
    # 一次把时间线取出来，避免 N+1 查询
    grouped: dict[int, list[ContactInteraction]] = {}
    for row in db.exec(select(ContactInteraction).order_by(ContactInteraction.at)).all():  # type: ignore[arg-type]
        grouped.setdefault(row.contact_id, []).append(row)

    snapshots = [_snapshot(db, contact, interactions=grouped.get(int(contact.id or 0), [])) for contact in contacts]
    return build_follow_ups(snapshots, now=moment, horizon_days=horizon_days, include_later=include_later)


# --------------------------------------------------------------------------- #
# 学习清单的原料
# --------------------------------------------------------------------------- #
def _weaknesses(db: Session) -> list[WeaknessInput]:
    """从 M5 的掌握度表取薄弱点（mastery 是 0–100，这里转成 0–1）。"""
    names = {int(topic.id or 0): topic.name for topic in db.exec(select(SkillTopic)).all()}
    rows: list[WeaknessInput] = []

    for row in db.exec(select(SkillMastery)).all():
        name = names.get(int(row.topic_id or 0))
        if not name:
            continue
        attempts = int(row.attempts or 0)
        accuracy = (float(row.correct_hits or 0) / attempts) if attempts else 0.0
        rows.append(
            WeaknessInput(
                topic=name,
                mastery=float(row.mastery or 0) / 100,
                accuracy=accuracy,
                attempts=attempts,
            )
        )
    return rows


def _resume_text(db: Session, resume_id: int | None = None) -> str:
    if resume_id is not None:
        resume = db.get(Resume, resume_id)
        return resume.content_text if resume is not None else ""
    latest = db.exec(select(Resume).order_by(Resume.id.desc())).first()  # type: ignore[attr-defined]
    return latest.content_text if latest is not None else ""


def _gaps(db: Session, *, resume_text: str | None = None, limit_apps: int = 50) -> list[GapInput]:
    """从投递记录的 JD 里统计"你缺哪些关键词、缺在多少份 JD 里"。

    这是 M8 与 M14 的真正联动：单看一份 JD 的缺失词，不知道它重不重要；
    看在多少份 JD 里都缺，就知道该先补哪个。
    """
    applications = [
        item
        for item in db.exec(select(Application).order_by(Application.id.desc())).all()  # type: ignore[arg-type]
        if (item.jd_text or "").strip()
    ][:limit_apps]
    if not applications:
        return []

    text = resume_text if resume_text is not None else _resume_text(db)
    counts: dict[str, int] = {}
    weights: dict[str, float] = {}

    for application in applications:
        result = match(text, application.jd_text)
        for keyword in result.missing:
            counts[keyword.word] = counts.get(keyword.word, 0) + 1
            weights[keyword.word] = max(weights.get(keyword.word, 0.0), float(keyword.weight))

    return [
        GapInput(word=word, weight=weights.get(word, 0.7), count=count, group=skill_group(word))
        for word, count in counts.items()
    ]


# --------------------------------------------------------------------------- #
# 学习清单
# --------------------------------------------------------------------------- #
def list_learning(db: Session, *, status: str | None = None) -> list[LearningItem]:
    statement = select(LearningItem)
    if status:
        statement = statement.where(LearningItem.status == status)
    return list(db.exec(statement.order_by(LearningItem.priority.desc())).all())  # type: ignore[arg-type]


def generate_path(
    db: Session,
    *,
    limit: int = 8,
    replace: bool = False,
    resume_id: int | None = None,
) -> list[LearningItem]:
    """重新生成学习清单（按 topic upsert，不会重复建项）。"""
    suggestions = build_path(
        _weaknesses(db),
        _gaps(db, resume_text=_resume_text(db, resume_id)),
        limit=limit,
    )

    if replace:
        # 只清"还没开始"的：进行中与已完成的保留——那是用户已经投入的时间
        for row in db.exec(select(LearningItem).where(LearningItem.status == "todo")).all():
            db.delete(row)
        db.commit()

    existing = {item.topic: item for item in list_learning(db)}
    saved: list[LearningItem] = []
    for suggestion in suggestions:
        item = existing.get(suggestion.topic) or LearningItem(topic=suggestion.topic)
        item.title = suggestion.title
        item.source = suggestion.source
        item.priority = suggestion.priority
        item.reason = suggestion.reason
        item.action = suggestion.action
        item.hours = suggestion.hours
        item.breakdown = dict(suggestion.breakdown)
        item.updated_at = utcnow()
        db.add(item)
        saved.append(item)

    db.commit()
    for item in saved:
        db.refresh(item)
    return saved


def update_learning(db: Session, item_id: int, *, payload: dict[str, Any]) -> LearningItem | None:
    item = db.get(LearningItem, item_id)
    if item is None:
        return None
    for key, value in payload.items():
        if hasattr(item, key):
            setattr(item, key, value)
    if item.status == "done" and item.done_at is None:
        item.done_at = utcnow()
    elif item.status != "done":
        item.done_at = None
    item.updated_at = utcnow()
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete_learning(db: Session, item_id: int) -> bool:
    item = db.get(LearningItem, item_id)
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def learning_summary(db: Session) -> dict[str, Any]:
    items = list_learning(db)
    done = [item for item in items if item.status == "done"]
    doing = [item for item in items if item.status == "doing"]
    todo = [item for item in items if item.status == "todo"]

    total_hours = round(sum(item.hours for item in items), 1)
    done_hours = round(sum(item.hours for item in done), 1)
    return {
        "total": len(items),
        "done": len(done),
        "doing": len(doing),
        "todo": len(todo),
        "hours_total": total_hours,
        "hours_done": done_hours,
        "progress": round(len(done) / len(items), 4) if items else 0.0,
    }


def learning_vs_mastery(db: Session) -> list[dict[str, Any]]:
    """把「我学完了」和「我掌握了」对上。

    学完但掌握度没动，说明练法不对（只看文档、没动手做）——
    这是这张表存在的意义：它让"我学了"和"我会了"的差距可见。
    """
    names = {int(topic.id or 0): topic.name for topic in db.exec(select(SkillTopic)).all()}
    mastery: dict[str, dict[str, Any]] = {}
    for row in db.exec(select(SkillMastery)).all():
        name = names.get(int(row.topic_id or 0))
        if name:
            mastery[name] = {
                "mastery": round(float(row.mastery or 0), 1),
                "attempts": int(row.attempts or 0),
                "correct_hits": int(row.correct_hits or 0),
            }

    items = {item.topic: item for item in list_learning(db) if item.topic}
    topics = sorted(set(mastery) | set(items))

    rows: list[dict[str, Any]] = []
    for topic in topics:
        stat = mastery.get(topic)
        has_data = stat is not None
        item = items.get(topic)
        status = item.status if item is not None else ""
        mastery_value = stat.get("mastery", 0.0) if has_data else 0.0

        gap = ""
        if status == "done" and has_data and mastery_value < 60:
            gap = "已标记学完，但掌握度仍低于 60——建议再练几道题，别只停留在看"
        elif status == "done" and not has_data:
            # **没数据 ≠ 掌握度低**：把它说成"掌握度 0"会让人以为白学了
            gap = "已标记学完，但还没有掌握度数据——去练几道题，让系统知道你到底会不会"
        elif status in {"todo", "doing"} and has_data and mastery_value >= 80:
            gap = "掌握度已经不错了，可以考虑先放下，把时间给更弱的地方"

        rows.append(
            {
                "topic": topic,
                "status": status,
                "priority": item.priority if item is not None else 0.0,
                "mastery": mastery_value,
                "has_data": has_data,
                "attempts": stat.get("attempts", 0) if has_data else 0,
                "note": gap,
            }
        )

    rows.sort(key=lambda row: (-row["priority"], row["mastery"]))
    return rows


def path_summary(db: Session) -> dict[str, Any]:
    """给前端显示的一句话概览（基于当前清单）。"""
    items = [
        LearningSuggestion(
            title=item.title,
            topic=item.topic,
            source=item.source,
            priority=item.priority,
            reason=item.reason,
            action=item.action,
            hours=item.hours,
            breakdown=dict(item.breakdown or {}),
        )
        for item in list_learning(db)
        if item.status != "dropped"
    ]
    return summarize_path(items)
