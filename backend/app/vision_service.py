"""机器视觉题库的编排（M13）：播种、查询、自测记录。

**为什么要把内置题库播种进数据库**，而不是每次直接读代码里的常量：

- 自测记录（`question_attempt`）需要外键指向题目，题目必须在表里有 id；
- 用户以后会想自己加题、改题，甚至把从别处收集的题导进来——
  内置题和用户题应该在同一个池子里，用 `source` 区分来源就够了；
- 播种是**幂等**的：按题干去重，重启不会产生重复行。

表结构完全复用 M0 就预留好的 `QuestionBank`（`sample_answer` / `reference_points`
正是为题库准备的），所以这一步不需要新表、也不需要迁移。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .models.core import QuestionAttempt, QuestionBank
from .vision.question import ROLE_DESCRIPTIONS, ROLE_LABELS, VisionQuestion, all_questions

#: 播种进库的题目用这个 source 标记，方便区分内置题与用户自建题
BUILTIN_SOURCE = "builtin:vision"

#: 进程内标记：播种是幂等的，没必要每次请求都查一遍去重
_seeded = False


class VisionError(RuntimeError):
    """业务错误：题目不存在、自评取值不合法。"""


def render_answer(question: VisionQuestion) -> str:
    """把答案拼成一份完整的 Markdown。

    追问与常见错误**并进答案正文**并存库：这样导出、或者直接看 `question_bank`
    表的时候，拿到的是完整的一份，而不是需要再去别处找的残缺片段。
    详情接口仍然返回结构化字段（`points` / `followups` / `pitfalls`），
    界面按结构化渲染。
    """
    parts = [question.answer.strip()]
    if question.followups:
        lines = "\n".join(f"- {item}" for item in question.followups)
        parts.append(f"## 面试官大概率会追问\n\n{lines}")
    if question.pitfalls:
        lines = "\n".join(f"- {item}" for item in question.pitfalls)
        parts.append(f"## 常见错误回答\n\n{lines}")
    return "\n\n".join(parts)


def ensure_seeded(db: Session) -> int:
    """把内置题库写进 `question_bank`（按题干去重）。返回本次新增的条数。"""
    global _seeded
    if _seeded:
        return 0

    existing = {
        row.text
        for row in db.exec(
            select(QuestionBank).where(QuestionBank.source == BUILTIN_SOURCE)  # type: ignore[arg-type]
        ).all()
    }

    added = 0
    for question in all_questions():
        if question.text in existing:
            continue
        db.add(
            QuestionBank(
                text=question.text,
                type=question.role,
                tags=[question.category],
                difficulty=question.difficulty,
                reference_points=list(question.points),
                sample_answer=render_answer(question),
                source=BUILTIN_SOURCE,
            )
        )
        added += 1

    if added:
        db.commit()
    _seeded = True
    return added


def reset_seed_flag() -> None:
    """测试用：清掉进程内标记，让下一次调用重新走一遍去重逻辑。"""
    global _seeded
    _seeded = False


def _attempt_stats(db: Session) -> dict[int, dict[str, object]]:
    """一次取出全部自测记录再在内存里聚合。

    题库是几十到几百条的量级，一次查询比按题逐条查（N+1）快得多，
    而且列表页每次都要显示进度。
    """
    stats: dict[int, dict[str, object]] = {}
    rows = db.exec(select(QuestionAttempt).order_by(QuestionAttempt.asked_at)).all()  # type: ignore[arg-type]
    for row in rows:
        if row.question_id is None:
            continue
        entry = stats.setdefault(row.question_id, {"attempts": 0, "last": "", "tally": {}})
        entry["attempts"] = int(entry["attempts"]) + 1
        rating = str(row.scores.get("self_rating") or "")
        if rating:
            entry["last"] = rating
            tally = entry["tally"]
            assert isinstance(tally, dict)
            tally[rating] = int(tally.get(rating, 0)) + 1
    return stats


def _row_to_brief(row: QuestionBank, stats: dict[int, dict[str, object]]) -> dict[str, object]:
    entry = stats.get(row.id or -1, {})
    role = row.type or "development"
    return {
        "id": row.id or 0,
        "text": row.text,
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "category": (row.tags or ["未分类"])[0],
        "difficulty": row.difficulty,
        "attempts": int(entry.get("attempts", 0)),
        "last_rating": str(entry.get("last", "")),
    }


def list_questions(
    db: Session,
    *,
    role: str | None = None,
    category: str | None = None,
    max_difficulty: int | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[dict[str, object]]:
    ensure_seeded(db)
    stats = _attempt_stats(db)

    statement = select(QuestionBank).where(QuestionBank.source == BUILTIN_SOURCE)  # type: ignore[arg-type]
    if role:
        statement = statement.where(QuestionBank.type == role)
    if max_difficulty is not None:
        statement = statement.where(QuestionBank.difficulty <= max_difficulty)
    if keyword:
        statement = statement.where(QuestionBank.text.contains(keyword))  # type: ignore[attr-defined]

    rows = list(db.exec(statement.order_by(QuestionBank.id)).all())  # type: ignore[attr-defined]
    items = [_row_to_brief(row, stats) for row in rows]
    if category:
        items = [item for item in items if item["category"] == category]
    return items[:limit]


def get_question(db: Session, question_id: int) -> dict[str, object] | None:
    ensure_seeded(db)
    row = db.get(QuestionBank, question_id)
    if row is None or row.source != BUILTIN_SOURCE:
        return None

    detail = _row_to_brief(row, _attempt_stats(db))
    source = next((item for item in all_questions() if item.text == row.text), None)
    if source is None:
        # 库里的题在代码题库里找不到（题库被改过）——仍然返回，但结构化字段为空，
        # 至少答案正文还在，不让用户看到一个 500
        detail.update({"points": row.reference_points or [], "answer": row.sample_answer, "followups": [], "pitfalls": []})
        return detail

    detail.update(
        {
            "points": list(source.points),
            "answer": source.answer,
            "followups": list(source.followups),
            "pitfalls": list(source.pitfalls),
        }
    )
    return detail


def meta(db: Session) -> dict[str, object]:
    ensure_seeded(db)
    rows = list(
        db.exec(select(QuestionBank).where(QuestionBank.source == BUILTIN_SOURCE)).all()  # type: ignore[arg-type]
    )
    counts: dict[str, int] = {}
    categories: list[str] = []
    for row in rows:
        counts[row.type] = counts.get(row.type, 0) + 1
        for tag in row.tags or []:
            if tag not in categories:
                categories.append(tag)

    return {
        "roles": [
            {
                "key": key,
                "label": ROLE_LABELS[key],
                "description": ROLE_DESCRIPTIONS[key],
                "count": counts.get(key, 0),
            }
            for key in ROLE_LABELS
        ],
        "categories": sorted(categories),
        "total": len(rows),
    }


def record_attempt(db: Session, *, question_id: int, rating: str, note: str = "") -> dict[str, object]:
    row = db.get(QuestionBank, question_id)
    if row is None or row.source != BUILTIN_SOURCE:
        raise VisionError(f"没有这道题：{question_id}")

    db.add(
        QuestionAttempt(
            question_id=question_id,
            session_id=None,  # 自测不属于任何一场模拟面试
            answer_text=note,
            scores={"self_rating": rating},
        )
    )
    db.commit()

    stats = _attempt_stats(db).get(question_id, {})
    tally = stats.get("tally", {})
    return {
        "question_id": question_id,
        "rating": rating,
        "attempts": int(stats.get("attempts", 0)),
        "last_rating": str(stats.get("last", "")),
        "tally": dict(tally) if isinstance(tally, dict) else {},
    }
