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

import hashlib
import random
import time
from typing import Sequence

from sqlmodel import Session, select

from .analysis.question_bank import BankQuestion
from .models.core import QuestionAttempt, QuestionBank
from .vision.import_doc import ImportedQuestion
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


def import_questions(
    db: Session,
    *,
    filename: str,
    questions: Sequence[ImportedQuestion],
    role: str = "uploaded",
) -> dict[str, object]:
    """把解析出来的题写进 `question_bank`，返回批次信息。

    题干已存在的会**跳过而不是报错**：用户很可能重复上传同一份文档
    （改了几道题再传一次），那时应该只补新增的，而不是整批失败或整批重复。

    批次用 `source="upload:<8 位短 id>"` 区分，原始文件名放在 tags 里。
    这样不用新建表也能回答「有哪些批次、每批多少题、来自哪个文件」。
    """
    batch = hashlib.sha1(f"{filename}|{time.time()}".encode()).hexdigest()[:8]
    source = f"upload:{batch}"
    existing = {row.text for row in db.exec(select(QuestionBank)).all()}

    added = 0
    skipped = 0
    for item in questions:
        if item.text in existing:
            skipped += 1
            continue
        tags = [f"上传:{filename}"]
        if item.category:
            tags.append(item.category)
        db.add(
            QuestionBank(
                text=item.text,
                type=role,
                tags=tags,
                difficulty=item.difficulty,
                reference_points=list(item.points),
                sample_answer=item.answer,
                source=source,
            )
        )
        existing.add(item.text)
        added += 1

    db.commit()
    return {
        "batch": batch,
        "source": source,
        "filename": filename,
        "role": role,
        "added": added,
        "skipped": skipped,
        "total": len(questions),
    }


def list_uploads(db: Session) -> list[dict[str, object]]:
    """已上传的批次（从库里聚合出来，不额外维护表）。"""
    rows = list(
        db.exec(
            select(QuestionBank).where(QuestionBank.source.startswith("upload:"))  # type: ignore[attr-defined]
        ).all()
    )
    batches: dict[str, dict[str, object]] = {}
    for row in rows:
        entry = batches.setdefault(
            row.source,
            {
                "source": row.source,
                "batch": row.source.split(":", 1)[1],
                "count": 0,
                "filename": "",
                "role": row.type,
            },
        )
        entry["count"] = int(entry["count"]) + 1
        for tag in row.tags or []:
            if tag.startswith("上传:") and not entry["filename"]:
                entry["filename"] = tag[3:]
    return sorted(batches.values(), key=lambda item: str(item["batch"]), reverse=True)


def delete_upload(db: Session, batch: str) -> int:
    """删掉整个批次。

    **先删引用它的自测记录**：`question_attempt.question_id` 有外键约束，
    直接删题会被数据库拦下来（`PRAGMA foreign_keys=ON`）。
    """
    source = f"upload:{batch}"
    rows = list(db.exec(select(QuestionBank).where(QuestionBank.source == source)).all())
    for row in rows:
        attempts = db.exec(
            select(QuestionAttempt).where(QuestionAttempt.question_id == row.id)
        ).all()
        for attempt in attempts:
            db.delete(attempt)
        db.delete(row)
    db.commit()
    return len(rows)


def pick_questions(
    db: Session,
    *,
    mix: dict[str, float] | None = None,
    role: str | None = None,
    category: str | None = None,
    max_difficulty: int | None = None,
    count: int = 6,
    rng: random.Random | None = None,
) -> list[BankQuestion]:
    """按**题源占比**抽题，用于「按题库组一套面试」。

    `mix` 形如 `{"builtin": 2, "uploaded": 1}`，表示内置题占 2/3、自己上传的占 1/3。
    传 None 或全为 0 时视为「只用内置」。名额按**最大余数法**分配，
    保证各来源相加正好等于要抽的题数——否则 6 道题按 1:1:1 分会变成 6 道或 3 道。

    某个来源的题不够时，用其他来源补足：宁可比例不准，也不能少给题。
    抽出来就固定下来（存进会话的 plan），面试过程中不再变。
    """
    ensure_seeded(db)
    rng = rng or random.Random()

    pools: dict[str, list[QuestionBank]] = {
        "builtin": _query_pool(db, source=BUILTIN_SOURCE, role=role, max_difficulty=max_difficulty),
        "uploaded": _query_pool(
            db, source_prefix="upload:", role=role, max_difficulty=max_difficulty
        ),
    }
    if category:
        for key, rows in pools.items():
            pools[key] = [row for row in rows if category in (row.tags or [])]

    weights = {key: max(0.0, float(value)) for key, value in (mix or {}).items() if value}
    if not weights:
        weights = {"builtin": 1.0}

    quota = _allocate(count, weights, available={key: len(rows) for key, rows in pools.items()})

    picked: list[QuestionBank] = []
    for source, take in quota.items():
        pool = pools.get(source, [])
        if take > 0 and pool:
            picked.extend(rng.sample(pool, min(take, len(pool))))

    # 某些来源不够（或 quota 里含空池）时补足，别少给题
    if len(picked) < count:
        chosen = {id(row) for row in picked}
        leftover = [row for rows in pools.values() for row in rows if id(row) not in chosen]
        short = count - len(picked)
        if leftover:
            picked.extend(rng.sample(leftover, min(short, len(leftover))))

    return [
        BankQuestion(
            id=row.id or 0,
            text=row.text,
            points=list(row.reference_points or []),
            category=(row.tags or [""])[0],
            source=row.source,
        )
        for row in picked
    ]


def _query_pool(
    db: Session,
    *,
    source: str | None = None,
    source_prefix: str | None = None,
    role: str | None = None,
    max_difficulty: int | None = None,
) -> list[QuestionBank]:
    statement = select(QuestionBank)
    if source:
        statement = statement.where(QuestionBank.source == source)
    if source_prefix:
        statement = statement.where(QuestionBank.source.startswith(source_prefix))  # type: ignore[attr-defined]
    if role:
        statement = statement.where(QuestionBank.type == role)
    if max_difficulty is not None:
        statement = statement.where(QuestionBank.difficulty <= max_difficulty)
    return list(db.exec(statement).all())


def _allocate(
    total: int,
    weights: dict[str, float],
    *,
    available: dict[str, int] | None = None,
) -> dict[str, int]:
    """按权重分配 total 个名额（最大余数法）。

    先按权重取整，剩下的名额按小数部分从大到小补——这样各来源之和恒等于 total。
    有余量的来源会先被削到它的存量，避免把名额分给一个空池子。
    """
    positive = {key: value for key, value in weights.items() if value > 0}
    if not positive or total <= 0:
        return {}

    # 只按「确实有题」的来源分配名额，避免把名额丢进空池子
    usable = positive
    if available:
        with_stock = {key: value for key, value in positive.items() if available.get(key, 0) > 0}
        if with_stock:
            usable = with_stock

    weight_sum = sum(usable.values())
    exact = {key: total * value / weight_sum for key, value in usable.items()}
    quotas = {key: int(value) for key, value in exact.items()}

    remainder = total - sum(quotas.values())
    ranked = sorted(exact, key=lambda key: exact[key] - quotas[key], reverse=True)
    for key in ranked[:remainder]:
        quotas[key] += 1

    # 不能超过该来源实际有多少题
    if available:
        for key in quotas:
            quotas[key] = min(quotas[key], available.get(key, quotas[key]))

    # 补上没参与分配的来源（值为 0）：调用方不必处理 KeyError，
    # 「某个来源这次没抽到题」和「这个来源不存在」是两回事
    for key in positive:
        quotas.setdefault(key, 0)
    return quotas


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
    # 上传的题把来源文件名也记在 tags 里（用来回答「这批来自哪个文件」），
    # 那不是分类，取分类时要跳过——否则分类会显示成「上传:面经.md」
    categories = [tag for tag in (row.tags or []) if not tag.startswith("上传:")]
    return {
        "id": row.id or 0,
        "text": row.text,
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "category": categories[0] if categories else "未分类",
        "difficulty": row.difficulty,
        "attempts": int(entry.get("attempts", 0)),
        "last_rating": str(entry.get("last", "")),
    }


def list_questions(
    db: Session,
    *,
    source: str | None = None,
    role: str | None = None,
    category: str | None = None,
    max_difficulty: int | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[dict[str, object]]:
    ensure_seeded(db)
    stats = _attempt_stats(db)

    # 不限定 source：内置题与自己上传的题都该出现在题库页里
    statement = select(QuestionBank)
    if source:
        statement = statement.where(QuestionBank.source == source)
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
    if row is None:
        return None

    detail = _row_to_brief(row, _attempt_stats(db))
    builtin = next((item for item in all_questions() if item.text == row.text), None)
    if builtin is None:
        # 上传的题没有代码题库可对照：要点与答案直接用库里的，
        # 追问与常见错误留空（有些上传的题本来就不涉及这两项），
        # 至少不让人看到一个 500
        detail.update(
            {
                "points": row.reference_points or [],
                "answer": row.sample_answer,
                "followups": [],
                "pitfalls": [],
            }
        )
        return detail

    detail.update(
        {
            "points": list(builtin.points),
            "answer": builtin.answer,
            "followups": list(builtin.followups),
            "pitfalls": list(builtin.pitfalls),
        }
    )
    return detail


def meta(db: Session) -> dict[str, object]:
    """题库总览：按岗位、分类、来源三个维度统计。

    来源统计是给「面试时各来源占多少」那个设置用的——没有它，用户没法判断
    「我上传的题够不够撑起想要的占比」。
    """
    ensure_seeded(db)
    rows = list(db.exec(select(QuestionBank)).all())

    counts: dict[str, int] = {}
    categories: list[str] = []
    source_counts: dict[str, int] = {}
    for row in rows:
        counts[row.type] = counts.get(row.type, 0) + 1
        origin = "uploaded" if row.source.startswith("upload:") else "builtin"
        source_counts[origin] = source_counts.get(origin, 0) + 1
        for tag in row.tags or []:
            if tag.startswith("上传:"):
                continue  # 文件名不是分类
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
        "sources": [
            {"key": "builtin", "label": "内置题库", "count": source_counts.get("builtin", 0)},
            {"key": "uploaded", "label": "我上传的", "count": source_counts.get("uploaded", 0)},
        ],
    }


def record_attempt(db: Session, *, question_id: int, rating: str, note: str = "") -> dict[str, object]:
    row = db.get(QuestionBank, question_id)
    if row is None:
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
