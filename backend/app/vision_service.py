"""机器视觉题库的编排（M13）：播种、查询、自测记录、删除。

**为什么要把内置题库播种进数据库**，而不是每次直接读代码里的常量：

- 自测记录（`question_attempt`）需要外键指向题目，题目必须在表里有 id；
- 用户以后会想自己加题、改题，甚至把从别处收集的题导进来——
  内置题和用户题应该在同一个池子里，用 `source` 区分来源就够了；
- 播种是**幂等**的：按题干去重，重启不会产生重复行。

表结构完全复用 M0 就预留好的 `QuestionBank`（`sample_answer` / `reference_points`
正是为题库准备的），所以这一步不需要新表、也不需要迁移。

**删除是打墓碑而不是删行**（`DELETED_TAG`，见下）：播种的幂等性反过来要求
"删过的内置题"必须留个记号，否则重启就会被重新加回来。
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Iterable
from typing import Sequence

from sqlmodel import Session, select

from .analysis.question_bank import BankQuestion, origin_of
from .analysis.question_generate import (
    MAX_GENERATE_COUNT,
    compose_answer_text,
    generate_questions,
    parse_generated,
)
from .models.core import DELETED_TAG, QuestionAttempt, QuestionBank
from .providers.llm.base import LLMClient
from .vision.import_doc import ImportedQuestion
from .vision.question import ROLE_DESCRIPTIONS, ROLE_LABELS, VisionQuestion, all_questions

#: 播种进库的题目用这个 source 标记，方便区分内置题与用户自建题
BUILTIN_SOURCE = "builtin:vision"

#: 草稿标记。带这个 tag 的题**不进抽题池、也不出现在题库页**——
#: 模型生成的题要用户看过、决定采纳，才进入可选范围。
#: 这样「是否添加到提问的题库」就等价于「是否采纳」，语义直接对应得上。
DRAFT_TAG = "草稿"

# 删除标记（墓碑）的常量定义在 `models/core.py`——训练抽题（M5）与导出也要用
# 同一个值，它们本来就依赖模型模块。删一道题**不是把行删掉**，而是打上这个 tag
# 藏起来：内置题是按题干幂等播种的（见 `ensure_seeded`），硬删之后下一次播种
# （重启、或清掉进程内标记）会把它原样加回来——「删了又自己回来」比删不掉更让人
# 困惑。所以行留着（外键与播种去重都还看得见它），但凡是要展示给人看、或要拿去
# 抽题的地方都跳过它。

#: tags 那一列存了三类信息，靠前缀区分（见 `_row_to_brief`）：
#: 「上传:文件名」是来源、「标记:高频」是题目性质、其余的当分类。
#: 前缀不是装饰——没有它，一道"有标记但没分类"的题会被显示成分类是「高频」。
_TAG_MARK_PREFIX = "标记:"

#: 用户自定义的标记标签长度上限。标签是给人扫一眼用的短词，
#: 超过这个长度基本就是想把一段话塞进标签里——那该写进答案或笔记。
MARK_TAG_MAX_LEN = 20

#: 模型出题时带上多少条已有题干（防止重复出题）。太多会把 prompt 撑爆，
#: 太少又挡不住重复——60 条是个折中。
EXISTING_HINT_LIMIT = 60

#: 已播种过的数据库身份（`id(engine)`）：播种是幂等的，没必要每次请求都查一遍去重。
#:
#: **为什么记"是哪个库"而不是一个简单的布尔值**：进程内的标记 + "每个测试一个全新数据库"
#: 会互相矛盾——库是新的、标记却还是 True，于是这个库从头到尾一道内置题都没有。
#: 表现出来就是一条与实现无关的偶发失败：同一个测试单独跑通过、在全量里失败。
#: 记库身份之后，"换了库就重新播种"是自动成立的。
_seeded_engine: int | None = None


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
    global _seeded_engine
    engine_key = id(db.get_bind())
    if _seeded_engine == engine_key:
        return 0

    # 这里**故意不筛掉墓碑**（已删除的内置题）：删掉的内置题必须继续算「已存在」，
    # 否则下一次播种就会把它原样加回来——用户会看到"删了又回来"。
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
    _seeded_engine = engine_key
    return added


def reset_seed_flag() -> None:
    """清掉"已播种"标记，让下一次调用重新走一遍去重逻辑（测试用）。"""
    global _seeded_engine
    _seeded_engine = None


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
    existing = _live_texts(db)

    added = 0
    skipped = 0
    for item in questions:
        if item.text in existing:
            skipped += 1
            continue
        tags = [f"上传:{filename}"]
        if item.category:
            tags.append(item.category)
        # 题目自带的标记（「高频」「易错」）也存下来——面试官反复问的题值得单独筛。
        # 加前缀，免得它被当成分类
        for tag in item.tags:
            marked = f"{_TAG_MARK_PREFIX}{tag}"
            if marked not in tags:
                tags.append(marked)
        db.add(
            QuestionBank(
                text=item.text,
                type=role,
                tags=tags,
                difficulty=item.difficulty,
                reference_points=list(item.points),
                sample_answer=item.answer,
                question_type=item.question_type,
                choice_options=list(item.options),
                correct_index=item.correct_index,
                explanation=item.explanation,
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
    """已上传的批次（从库里聚合出来，不额外维护表）。

    已删除的题不计入批次数量：用户删掉了这批里的一道题，批次上还显示原来的题数
    会让人以为删除没生效。
    """
    rows = [
        row
        for row in db.exec(
            select(QuestionBank).where(QuestionBank.source.startswith("upload:"))  # type: ignore[attr-defined]
        ).all()
        if not is_deleted(row)
    ]
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
    return _delete_batch_rows(db, prefix="upload", batch=batch)


# --------------------------------------------------------------------------- #
# 删除单题 / 批量删除
# --------------------------------------------------------------------------- #


def is_deleted(row: QuestionBank) -> bool:
    """这行是不是已经被删除（墓碑）。"""
    return DELETED_TAG in (row.tags or [])


def _visible_rows(rows: Iterable[QuestionBank]) -> list[QuestionBank]:
    """筛出「还在题库里」的题：草稿与已删除的都不算。

    展示（列表 / 统计 / 上传批次）、抽题、给模型当「别重复」的素材，都该走这个筛选，
    否则删掉的题总会在某个角落里冒出来。
    """
    return [
        row for row in rows if DRAFT_TAG not in (row.tags or []) and not is_deleted(row)
    ]


def _live_texts(db: Session) -> set[str]:
    """库里「还在用」的题干（墓碑不算）。

    去重要用这个而不是全表题干：用户删掉一道题之后，重新导入同一份文档、或让模型
    再出一次，应该能把它加回来——否则删一次就等于永久拉黑这个题干，
    而且界面上没有任何提示，只有"导入说成功、列表里却没有"。
    """
    return {row.text for row in db.exec(select(QuestionBank)).all() if not is_deleted(row)}


def delete_question(db: Session, question_id: int) -> bool:
    """删除一道**已入库**的题，连带它的自测记录。返回是否真删到了。

    草稿不算「已入库」：它们还没进题库页、也没进抽题池，该走
    `DELETE /api/vision/drafts/{batch}`，这里对它返回 False。
    已经删过的再删也返回 False——重复点不会得到"删掉了两次"的错觉。
    """
    row = db.get(QuestionBank, question_id)
    if row is None or DRAFT_TAG in (row.tags or []) or is_deleted(row):
        return False
    _mark_deleted(db, row)
    db.commit()
    return True


def delete_questions(db: Session, question_ids: Sequence[int]) -> int:
    """批量删除（多选删除用）。返回真正删掉的条数。

    不存在、是草稿、已经删过的都不计入——所以前端可以把整批 id 再传一次，
    结果是 0 而不是报错。
    """
    if not question_ids:
        return 0

    rows = db.exec(select(QuestionBank).where(QuestionBank.id.in_(list(question_ids)))).all()  # type: ignore[attr-defined]

    removed = 0
    for row in rows:
        if DRAFT_TAG in (row.tags or []) or is_deleted(row):
            continue
        _mark_deleted(db, row)
        removed += 1
    if removed:
        db.commit()
    return removed


def _mark_deleted(db: Session, row: QuestionBank) -> None:
    """打上墓碑，并把这道题的自测记录一起删掉。

    自测记录留着就是**悬挂引用**：题已经从列表里消失了，那些记录再也没有入口，
    却还会被 `_attempt_stats` 统计到一个取不到的 question_id 上；
    `question_attempt.question_id` 本来就有外键约束，清掉才是干净的做法。
    """
    attempts = db.exec(select(QuestionAttempt).where(QuestionAttempt.question_id == row.id)).all()
    for attempt in attempts:
        db.delete(attempt)
    # 赋新列表而不是 append：JSON 列的原地修改 SQLAlchemy 检测不到
    row.tags = [*(row.tags or []), DELETED_TAG]
    db.add(row)


# --------------------------------------------------------------------------- #
# 模型生成：草稿 → 采纳
# --------------------------------------------------------------------------- #
GENERATE_SOURCE_PREFIX = "generated"


def _delete_batch_rows(db: Session, *, prefix: str, batch: str) -> int:
    source = f"{prefix}:{batch}"
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


def _rows_of_batch(db: Session, batch: str) -> list[QuestionBank]:
    source = f"{GENERATE_SOURCE_PREFIX}:{batch}"
    return list(db.exec(select(QuestionBank).where(QuestionBank.source == source)).all())


def _existing_hints(db: Session, *, exclude_batch: str | None, own: list[QuestionBank]) -> list[BankQuestion]:
    """给模型的「不要重复这些」清单。

    本批次已有的题干排最前——点「继续生成」时最要紧的就是别和刚生成的撞车。
    再补上题库里其他题（内置、上传、已采纳的），避免和现有素材重复。
    """
    hints: list[BankQuestion] = []

    def add(row: QuestionBank) -> None:
        hints.append(
            BankQuestion(
                id=row.id or 0,
                text=row.text,
                points=[],
                category=(row.tags or [""])[0],
                source=row.source,
            )
        )

    for row in own:
        add(row)

    others = list(db.exec(select(QuestionBank)).all())
    for row in others:
        if len(hints) >= EXISTING_HINT_LIMIT:
            break
        # 已删除的题不再当素材：它既不会被抽到，也没必要占"别重复"的名额
        if is_deleted(row):
            continue
        if exclude_batch and row.source == f"{GENERATE_SOURCE_PREFIX}:{exclude_batch}":
            continue
        add(row)

    return hints[:EXISTING_HINT_LIMIT]


async def generate_draft(
    db: Session,
    *,
    llm: LLMClient,
    role: str,
    count: int,
    category: str | None = None,
    difficulty: int | None = None,
    question_type: str = "open",
    domain: str | None = None,
    mode: str = "new",
    batch: str | None = None,
    note: str = "",
) -> dict[str, object]:
    """让模型出一批题，**以草稿状态入库**（带 `草稿` tag，不进抽题池）。

    `mode`：
    - `new`：新建一个批次；
    - `append`：在 `batch` 上继续生成——把该批已有题干带给模型，要求它别重复；
    - `replace`：丢掉 `batch` 的现有草稿，按这次给的参数重新生成一批。

    `question_type`：open（问答题）| choice（选择题）。选择题的选项与正确下标
    由模型给出、存进库；它们只参与「生成 → 作答看解析 → 采纳」的自测闭环，
    抽题时会被排除（面试是问答场景）。

    生成结果**不自动进入抽题池**：用户要看过、决定采纳。这样「选择是否添加到
    提问的题库」就落在了一个明确动作上，而不是靠默认行为。
    """
    count = max(1, min(count, MAX_GENERATE_COUNT))

    own: list[QuestionBank] = []
    if mode in ("append", "replace") and batch:
        found = _rows_of_batch(db, batch)
        if not found:
            raise VisionError(f"没有这个生成批次：{batch}")
        if mode == "replace":
            _delete_batch_rows(db, prefix=GENERATE_SOURCE_PREFIX, batch=batch)
        else:
            own = found
    if mode == "new" or not batch:
        batch = hashlib.sha1(f"{role}|{time.time()}".encode()).hexdigest()[:8]

    # 重新生成时，被丢掉那批的题干也不该再出现——所以它们要从 hints 里排除掉
    hints = _existing_hints(db, exclude_batch=None if mode == "replace" else batch, own=own)

    questions = await generate_questions(
        llm,
        role=role,
        count=count,
        difficulty=difficulty,
        category=category,
        question_type=question_type,
        domain=domain,
        existing=hints,
        extra_note=note,
    )
    if not questions:
        return {
            "batch": batch or "",
            "mode": mode,
            "added": 0,
            "skipped": 0,
            "drafts": _drafts_out(db, batch) if batch else [],
            "note": "模型这次没有给出可解析的题目，可以直接重试。",
        }

    source = f"{GENERATE_SOURCE_PREFIX}:{batch}"
    existing_texts = _live_texts(db)
    added = 0
    skipped = 0
    for item in questions:
        if item.text in existing_texts:
            skipped += 1
            continue
        tags = [DRAFT_TAG, item.category or "未分类"]
        db.add(
            QuestionBank(
                text=item.text,
                type=role,
                tags=[tag for tag in tags if tag],
                difficulty=item.difficulty,
                reference_points=list(item.points),
                sample_answer=compose_answer_text(item.answer, item.followups, item.pitfalls),
                question_type=item.question_type,
                choice_options=list(item.options),
                correct_index=item.correct_index,
                explanation=item.explanation,
                source=source,
            )
        )
        existing_texts.add(item.text)
        added += 1

    db.commit()
    return {
        "batch": batch,
        "mode": mode,
        "added": added,
        "skipped": skipped,
        "drafts": _drafts_out(db, batch),
        "note": "" if added else "生成的题目与题库里已有的重复，都被跳过了。",
    }


def _drafts_out(db: Session, batch: str) -> list[dict[str, object]]:
    rows = _rows_of_batch(db, batch)
    return [_row_to_brief(row, {}) for row in rows]


def list_drafts(db: Session) -> list[dict[str, object]]:
    """所有草稿批次（带题数），供生成面板展示。"""
    rows = [
        row
        for row in db.exec(
            select(QuestionBank).where(
                QuestionBank.source.startswith(f"{GENERATE_SOURCE_PREFIX}:")  # type: ignore[attr-defined]
            )
        ).all()
        if DRAFT_TAG in (row.tags or [])
    ]
    batches: dict[str, int] = {}
    for row in rows:
        batches[row.source] = batches.get(row.source, 0) + 1
    return [
        {"batch": source.split(":", 1)[1], "count": count, "source": source}
        for source, count in sorted(batches.items(), reverse=True)
    ]


def adopt_draft(db: Session, batch: str) -> int:
    """采纳：去掉草稿标记，这批题进入抽题池。"""
    rows = _rows_of_batch(db, batch)
    if not rows:
        raise VisionError(f"没有这个生成批次：{batch}")

    adopted = 0
    for row in rows:
        if DRAFT_TAG not in (row.tags or []):
            continue
        row.tags = [tag for tag in (row.tags or []) if tag != DRAFT_TAG]
        db.add(row)
        adopted += 1
    db.commit()
    return adopted


def adopt_questions(db: Session, question_ids: Sequence[int]) -> int:
    """按题采纳：只把指定的这几道草稿放进抽题池。

    与 `adopt_draft` 的区别是**粒度**——生成一批 5 道，你可能只满意其中 2 道。

    只认「模型生成 + 还是草稿」的题，两条都满足才动：
    这个接口不该被用来改已有的题，也不该能采纳非生成来源的题。
    已经采纳过的不重复计数，所以前端可以把整批 id 直接传过来。
    """
    if not question_ids:
        return 0

    rows = db.exec(select(QuestionBank).where(QuestionBank.id.in_(list(question_ids)))).all()  # type: ignore[attr-defined]

    adopted = 0
    for row in rows:
        if DRAFT_TAG not in (row.tags or []):
            continue
        if not str(row.source or "").startswith(f"{GENERATE_SOURCE_PREFIX}:"):
            continue
        row.tags = [tag for tag in (row.tags or []) if tag != DRAFT_TAG]
        db.add(row)
        adopted += 1
    db.commit()
    return adopted


def discard_draft(db: Session, batch: str) -> int:
    """丢弃：整批删掉（连带自测记录）。"""
    rows = _rows_of_batch(db, batch)
    if not rows:
        raise VisionError(f"没有这个生成批次：{batch}")
    return _delete_batch_rows(db, prefix=GENERATE_SOURCE_PREFIX, batch=batch)


def pick_questions(
    db: Session,
    *,
    mix: dict[str, float] | None = None,
    role: str | None = None,
    category: str | None = None,
    max_difficulty: int | None = None,
    count: int = 6,
    rng: random.Random | None = None,
    weak: bool = False,
) -> list[BankQuestion]:
    """按**题源占比**抽题，用于「按题库组一套面试」。

    `mix` 形如 `{"builtin": 2, "uploaded": 1}`，表示内置题占 2/3、自己上传的占 1/3。
    传 None 或全为 0 时视为「只用内置」。名额按**最大余数法**分配，
    保证各来源相加正好等于要抽的题数——否则 6 道题按 1:1:1 分会变成 6 道或 3 道。

    某个来源的题不够时，用其他来源补足：宁可比例不准，也不能少给题。
    抽出来就固定下来（存进会话的 plan），面试过程中不再变。

    M18：`weak=True`（薄弱点面试）时忽略来源占比，**优先抽标签命中薄弱能力点的题**——
    掌握度最低的技能点排最前，先从命中这些点的题里抽满，不足再从其余题补足。
    """
    ensure_seeded(db)
    rng = rng or random.Random()

    pools: dict[str, list[QuestionBank]] = {
        "builtin": _query_pool(db, source=BUILTIN_SOURCE, role=role, max_difficulty=max_difficulty),
        "uploaded": _query_pool(
            db, source_prefix="upload:", role=role, max_difficulty=max_difficulty
        ),
        # 模型生成的题：采纳之后才会出现在这个池子里（_query_pool 会滤掉草稿）
        "generated": _query_pool(
            db, source_prefix=f"{GENERATE_SOURCE_PREFIX}:", role=role, max_difficulty=max_difficulty
        ),
    }
    if category:
        for key, rows in pools.items():
            pools[key] = [row for row in rows if category in (row.tags or [])]

    if weak:
        return _pick_weak_questions(db, pools, count=count, rng=rng)

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


def _pick_weak_questions(
    db: Session,
    pools: dict[str, list[QuestionBank]],
    *,
    count: int,
    rng: random.Random,
) -> list[BankQuestion]:
    """M18：薄弱点面试的抽题核心。

    1. 取掌握度最低的 `count` 个技能点（无任何掌握度记录时用默认薄弱方向）；
    2. 从所有池子里筛「标签命中薄弱技能点」的题（互相包含即命中）；
    3. 从命中题里随机抽满 count；命中不足再用其余题补足，绝不因抽题让面试开不起来。
    """
    from .learning_service import mastery_overview
    from .training_service import DEFAULT_WEAK_TOPICS

    overview = mastery_overview(db)
    weak_names = [str(item["name"]) for item in overview[: max(1, count)]] or list(DEFAULT_WEAK_TOPICS)

    def hits_topic(tags: list[str]) -> bool:
        for tag in tags:
            a = (tag or "").strip().lower()
            for name in weak_names:
                b = (name or "").strip().lower()
                if a and b and (a in b or b in a):
                    return True
        return False

    all_rows: list[QuestionBank] = [row for rows in pools.values() for row in rows]
    matched = [row for row in all_rows if hits_topic(list(row.tags or []))]
    leftover = [row for row in all_rows if row not in matched]

    picked: list[QuestionBank] = []
    if matched:
        picked.extend(rng.sample(matched, min(count, len(matched))))
    if len(picked) < count and leftover:
        picked.extend(rng.sample(leftover, min(count - len(picked), len(leftover))))

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

    # 草稿不进抽题池：模型生成的东西要人看过才该被问到；
    # 已删除的题同样不进——否则"删掉的题还在面试里被问到"；
    # 选择题不进抽题池：面试是问答场景，选项题不适用，
    # 它们只作为「生成 → 作答看解析 → 采纳进题库」的自测资源
    return [
        row
        for row in db.exec(statement).all()
        if DRAFT_TAG not in (row.tags or [])
        and not is_deleted(row)
        and (row.question_type or "open") != "choice"
    ]


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
    # tags 一列存了三类东西，靠前缀区分：
    #   「上传:文件名」= 来源批次（用来回答"这批来自哪个文件"）
    #   「标记:高频」  = 题目性质（文档自带的标注）
    #   其余          = 分类
    # 分类必须能可靠地取到——不加前缀的话，一道"有标记但没分类"的题
    # 会被显示成分类是「高频」。
    tags = [tag for tag in (row.tags or []) if not tag.startswith("上传:")]
    categories = [tag for tag in tags if not tag.startswith(_TAG_MARK_PREFIX)]
    return {
        "id": row.id or 0,
        "text": row.text,
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "category": categories[0] if categories else "未分类",
        "difficulty": row.difficulty,
        "question_type": row.question_type or "open",
        "tags": [tag.removeprefix(_TAG_MARK_PREFIX) for tag in tags],
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
    tag: Iterable[str] | None = None,
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

    rows = list(db.exec(statement.order_by(QuestionBank.id)).all())  # type: ignore[attr-defined]
    # 草稿只在生成面板里管，题库页不显示；已删除的题当然也不显示
    rows = _visible_rows(rows)
    if keyword:
        # 关键词不只搜题干，也搜标签（「高频」「易错」这类性质标签和分类名）：
        # 在搜索框输入「高频」，就该搜到所有带"高频"标签的题——哪怕题干里
        # 根本没有这两个字。范围 = 题干 ∪ 分类名 ∪ 标记标签名。
        # 题量不大（几百道），内存里过滤没有性能问题，而且 SQL 的 contains
        # 表达不了"题干或标签"这种跨列匹配。
        kw = keyword.strip()
        if kw:
            rows = [
                row
                for row in rows
                if kw in (row.text or "")
                or any(
                    kw in tag.removeprefix(_TAG_MARK_PREFIX)
                    for tag in (row.tags or [])
                    if not tag.startswith("上传:")  # 文件名不参与搜索
                )
            ]
    if tag:
        # 按标记标签筛（高频 / 易错 / 自定义）：**多选且取交集**——
        # 选了「高频」+「易错」时，只有两个标签都带上的题才算命中。
        # 匹配的是带前缀的原始 tag（`标记:高频`），不是显示名：
        # 一道"分类恰好叫高频"的题不该被这个筛子捞出来。
        for name in tag:
            marked = f"{_TAG_MARK_PREFIX}{name}"
            rows = [row for row in rows if marked in (row.tags or [])]
    items = [_row_to_brief(row, stats) for row in rows]
    if category:
        # 支持**按父主题筛**：分类形如「相机与图像基础 / 相机二开」，
        # 只做精确匹配的话，点父主题会一道题都筛不出来（实测 81 个主题就是这么堆起来的：
        # 40 多个只有 1 道题的细主题平铺在一排标签里，根本没法用）。
        prefix = f"{category} / "
        items = [item for item in items if item["category"] == category or item["category"].startswith(prefix)]
    return items[:limit]


def get_question(db: Session, question_id: int) -> dict[str, object] | None:
    ensure_seeded(db)
    row = db.get(QuestionBank, question_id)
    # 已删除的题按"不存在"处理：它的详情页不该还能打开
    if row is None or is_deleted(row):
        return None

    detail = _row_to_brief(row, _attempt_stats(db))
    # 选择题的选项与答案只放在详情里：列表页显示题型就够，不该提前泄露答案
    detail.update(
        {
            "choice_options": list(row.choice_options or []),
            "correct_index": row.correct_index,
            "explanation": row.explanation or "",
        }
    )
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
    「我上传的题够不够撑起想要的占比」。**草稿不计入来源统计**（它们不在抽题池里），
    但会单独给出数量。
    """
    ensure_seeded(db)
    rows = _visible_rows(db.exec(select(QuestionBank)).all())

    counts: dict[str, int] = {}
    categories: list[str] = []
    source_counts: dict[str, int] = {}
    mark_counts: dict[str, int] = {}
    for row in rows:
        counts[row.type] = counts.get(row.type, 0) + 1
        origin = _origin_of(row.source)
        source_counts[origin] = source_counts.get(origin, 0) + 1
        for tag in row.tags or []:
            if tag.startswith("上传:"):
                continue  # 文件名不是分类
            if tag.startswith(_TAG_MARK_PREFIX):
                # 「标记:高频」「标记:易错」是**题目性质**，不是主题。
                # 以前只挡了「上传:」，于是这两个跑进主题列表，用户看到的主题又多又怪。
                # 它们单独聚合成「标记标签」，供筛选器按标签筛题。
                name = tag.removeprefix(_TAG_MARK_PREFIX)
                mark_counts[name] = mark_counts.get(name, 0) + 1
                continue
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
        #: 两级主题：`父` 与它下面的 `子`。分类字符串形如「相机与图像基础 / 相机二开」，
        #: 平铺展示会变成 81 个标签（其中 40 多个只有 1 道题），所以界面上只平铺父主题，
        #: 选中父主题后再出子主题——数据结构在这里算好，前端不用自己拆字符串。
        "topics": _topic_tree(rows),
        #: 标记标签按题数降序排：高频这类覆盖面大的排前面，冷门的自定义标签沉底
        "mark_tags": [
            {"name": name, "count": count}
            for name, count in sorted(mark_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "total": len(rows),
        "sources": [
            {"key": "builtin", "label": "内置题库", "count": source_counts.get("builtin", 0)},
            {"key": "uploaded", "label": "我上传的", "count": source_counts.get("uploaded", 0)},
            {"key": "generated", "label": "模型生成", "count": source_counts.get("generated", 0)},
        ],
        "draft_count": _draft_count(db),
    }


#: 分类字符串里父子之间的分隔符（导入时由「章节 + 题目括号」拼出来）
TOPIC_SEPARATOR = " / "


def _topic_tree(rows: Sequence[QuestionBank]) -> list[dict[str, object]]:
    """把分类聚成两级：`[{name, count, children: [{name, count, full}]}]`。

    只有**带子主题**的父主题才会列出 children；子主题名里如果还有分隔符，只再拆一层
    （文档最多就是"章节 / 题目括号"两级，多出来的层级并回父名，不制造第三级）。
    """
    child_counts: dict[str, dict[str, int]] = {}
    own_counts: dict[str, int] = {}
    for row in rows:
        tags = [tag for tag in (row.tags or []) if not tag.startswith(("上传:", _TAG_MARK_PREFIX))]
        category = tags[0] if tags else "未分类"
        parent, _, child = category.partition(TOPIC_SEPARATOR)
        parent = parent.strip() or "未分类"
        own_counts[parent] = own_counts.get(parent, 0) + 1
        if child.strip():
            bucket = child_counts.setdefault(parent, {})
            bucket[child.strip()] = bucket.get(child.strip(), 0) + 1

    topics: list[dict[str, object]] = []
    for parent, count in sorted(own_counts.items(), key=lambda item: (-item[1], item[0])):
        children = [
            {"name": name, "count": number, "full": f"{parent}{TOPIC_SEPARATOR}{name}"}
            for name, number in sorted(child_counts.get(parent, {}).items(), key=lambda item: (-item[1], item[0]))
        ]
        topics.append({"name": parent, "count": count, "children": children})
    return topics


def _origin_of(source: str) -> str:
    """把 `source` 归一成来源标签，用于统计与占比。

    实现在 `analysis/question_bank.origin_of`——那里是唯一定义处，
    免得两处漂移（之前就因此把模型生成的题算成了内置题）。
    """
    return origin_of(source)


def _draft_count(db: Session) -> int:
    return len(
        [
            row
            for row in db.exec(select(QuestionBank).where(QuestionBank.source.startswith("generated:"))).all()  # type: ignore[attr-defined]
            if DRAFT_TAG in (row.tags or [])
        ]
    )


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


# --------------------------------------------------------------------------- #
# 题目标记标签：用户自己给题目添加 / 移除「高频」「易错」等
# --------------------------------------------------------------------------- #


def _normalize_mark_tag(raw: str) -> str:
    """把用户给的标签清洗成可入库的「标记」值；不合法返回空串。

    用户标签只会被当作**题目性质**（`标记:` 前缀那一类），所以凡是会破坏
    「来源 / 标记 / 分类」区分、或撞上系统保留约定的取值都要挡掉：
    空串、超过长度上限、以「上传: / 标记:」开头的（会伪装成来源或重复前缀）、
    「已删除 / 草稿」（系统内部标记），以及含分类分隔符「 / 」的
    （会跟两级主题的"父 / 子"筛选语义混淆）。
    """
    clean = (raw or "").strip()
    if not clean or len(clean) > MARK_TAG_MAX_LEN:
        return ""
    if clean.startswith("上传:") or clean.startswith(_TAG_MARK_PREFIX):
        return ""
    if clean in (DELETED_TAG, DRAFT_TAG):
        return ""
    if TOPIC_SEPARATOR in clean:
        return ""
    return clean


def update_question_tags(
    db: Session,
    *,
    question_id: int,
    add: Iterable[str] | None = None,
    remove: Iterable[str] | None = None,
) -> dict[str, object] | None:
    """给一道题添加 / 移除「标记」标签。返回更新后的清单信息，题目不存在返回 None。

    只动 `标记:` 那一类：分类取 tags 的第一个元素，来源是「上传:」前缀——
    这两个是题目自身的属性，不该被"打标签"这个动作改掉。
    添加的标签**只追加到末尾**（插到前面会把第一个分类挤掉，`_row_to_brief`
    就会把标记当成分类显示）。

    移除不存在的标签是静默的：重复点击、或标签早已被摘掉时，不报错也不计数。
    """
    row = db.get(QuestionBank, question_id)
    if row is None or is_deleted(row):
        return None

    tags = list(row.tags or [])
    for raw in add or []:
        clean = _normalize_mark_tag(raw)
        if not clean:
            continue
        marked = f"{_TAG_MARK_PREFIX}{clean}"
        if marked not in tags:
            tags.append(marked)
    for raw in remove or []:
        clean = _normalize_mark_tag(raw)
        if not clean:
            continue
        marked = f"{_TAG_MARK_PREFIX}{clean}"
        tags = [tag for tag in tags if tag != marked]

    if tags != (row.tags or []):
        # 赋新列表而不是原地改：JSON 列的原地修改 SQLAlchemy 检测不到
        row.tags = tags
        db.add(row)
        db.commit()
    return _row_to_brief(row, _attempt_stats(db))
