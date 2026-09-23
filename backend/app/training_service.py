"""今日训练：挑题 → 作答 → 即时批改 → 更新掌握度。

两条设计决定：

1. **抽题用加权和，不是"掌握度最低的 N 个"**。否则会一直练同一批题、覆盖不到岗位要求，
   单选题型还会养成应试惯性。权重与每项贡献见 `analysis/selector.py`。
2. **题库优先，缺题就用 LLM 现场生成并写进题库**。这样 M5 不依赖 M2 的题库功能就能自成闭环，
   同时这些生成的题会留在题库里，后续可以直接复用。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlmodel import Session, select

from .analysis.mastery import MASTERY_MAX
from .analysis.selector import (
    Candidate,
    SelectorWeights,
    apply_type_penalty,
    explain,
    jd_match_score,
    # 起别名：直接叫 select 会覆盖 sqlmodel 的查询 select（排查成本很高，别踩第二次）
    select as select_candidates,
)
from .learning_service import (
    MAX_TOPICS_PER_OBSERVATION,
    apply_observation,
    due_topic_ids,
    ensure_topic,
    mastery_overview,
)
from .models.core import JobTarget, QuestionAttempt, QuestionBank
from .providers.llm.base import (
    RECORD_MARKER_END,
    RECORD_MARKER_START,
    LLMClient,
    LLMError,
    LLMMessage,
    extract_json,
)
from .services import log_llm_error, log_llm_result

TRAINING_QUESTION_PROMPT = """你是面试教练，正在针对候选人的薄弱能力点出练习题。

要求：
1. 每道题都是面试官真会问出口的一句话（可以带一点压力），不要写成知识点问答。
2. 每题给 3 条参考要点，说明好答案需要覆盖什么。
3. difficulty 取 1–5：3 表示常规，4–5 表示需要深挖细节。
4. tags 用给定的薄弱标签原文，便于系统把练习结果累计回对应能力。
5. 不要与记录里 avoid 列表的题干重复——那些是已经出过的题。
6. 全部中文。

只输出 JSON，不要输出其他内容：
{"questions": [{"text": "题干", "type": "training", "difficulty": 3, "tags": ["标签"], "reference_points": ["要点1", "要点2", "要点3"]}]}
"""

TRAINING_GRADE_PROMPT = """你是面试教练，正在批改一道练习题的回答。

要求：
1. score 用 1–5 分，要有区分度，不要一律给 3–4 分。
2. comment 用一句话指出**最该改的那一点**，具体到哪句话，不要说空话。
3. better_answer 用候选人自己的信息重写一版；信息不足就写"补上具体数字"这类占位提示，不要编造。
4. 全部中文。

只输出 JSON，不要输出其他内容：
{"score": 1-5, "comment": "一句话点评", "better_answer": "重写的一版"}
"""


class TrainingError(RuntimeError):
    """训练相关业务错误（题目不存在、回答为空等）。"""


# 新用户还没有任何掌握度记录时用的兜底标签：不然训练页会是空白。
# 特意选了覆盖面广、且每轮都能练出东西的方向（Mock 出题模板也覆盖了这三个）。
DEFAULT_WEAK_TOPICS: tuple[str, ...] = ("结构化表达", "结果导向", "技术取舍")


@dataclass(slots=True)
class TrainingQuestion:
    question_id: int
    text: str
    type: str
    difficulty: int
    tags: list[str] = field(default_factory=list)
    topic_names: list[str] = field(default_factory=list)
    origin: str = "bank"
    kind: str = "weak"  # weak | random
    reason: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MasteryChange:
    topic_id: int
    name: str
    before: float
    after: float


@dataclass(slots=True)
class TrainingGrade:
    question_id: int
    attempt_id: int
    score: int
    comment: str
    better_answer: str
    mastery: list[MasteryChange] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _contains(left: str, right: str) -> bool:
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    if not a or not b:
        return False
    return a in b or b in a


def _jd_keywords(jd_text: str, *, limit: int = 12) -> list[str]:
    """从 JD 里抠出关键词：够用即可，不做分词器。

    单用户场景下 JD 只有一篇，用"按行/分隔符切开 + 去停用词 + 取长度 2–12 的中英文片段"
    得到的集合小而准，且命中情况可以直接解释给用户看。
    """
    if not jd_text:
        return []

    separators = "，。；、,.;:：/|\n\t（）()[]【】“”\"' 　"
    for separator in separators:
        jd_text = jd_text.replace(separator, " ")

    stop = {"要求", "负责", "熟悉", "优先", "以及", "具备", "相关", "经验", "能力", "以上", "我们", "岗位", "工作"}
    seen: list[str] = []
    for token in jd_text.split():
        word = token.strip()
        if len(word) < 2 or len(word) > 12 or word in stop:
            continue
        if word not in seen:
            seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def _marker_payload(payload: dict[str, Any]) -> str:
    return f"{RECORD_MARKER_START}\n{json.dumps(payload, ensure_ascii=False)}\n{RECORD_MARKER_END}"


def _clamp_score(value: Any, *, low: int = 1, high: int = 5, default: int = 3) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _recent_training_types(db: Session, *, limit: int = 8) -> list[str]:
    rows = db.exec(
        select(QuestionAttempt).order_by(QuestionAttempt.id.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    types: list[str] = []
    for row in rows:
        if row.question_id is None:
            continue
        question = db.get(QuestionBank, row.question_id)
        if question is not None:
            types.append(question.type or "general")
    return types


# --------------------------------------------------------------------------- #
# 挑题
# --------------------------------------------------------------------------- #
async def _generate_questions(
    db: Session,
    *,
    llm: LLMClient,
    topics: Sequence[str],
    target_label: str,
    count: int,
) -> list[QuestionBank]:
    if count <= 0 or not topics:
        return []

    # 把题库里已出过的题干一起给模型：既省得它重复生成，也让去重不再"悄悄吃掉"配额
    existing_texts = [
        str(text)
        for text in db.exec(
            select(QuestionBank.text).order_by(QuestionBank.id.desc()).limit(50)  # type: ignore[arg-type]
        ).all()
    ]

    messages = [
        LLMMessage(role="system", content=TRAINING_QUESTION_PROMPT),
        LLMMessage(
            role="user",
            content=_marker_payload(
                {
                    "topics": list(topics),
                    "count": count,
                    "target": target_label,
                    "avoid": existing_texts,
                }
            ),
        ),
    ]

    try:
        result = await llm.complete(task="training_question", messages=messages, temperature=0.7, json_object=True)
    except LLMError as exc:
        log_llm_error(db, task="training_question", provider=getattr(llm, "name", ""), error=str(exc))
        return []

    log_llm_result(db, result)

    try:
        data = extract_json(result.text)
    except ValueError:
        return []

    created: list[QuestionBank] = []
    seen_texts: set[str] = set()
    raw_questions = data.get("questions")
    for item in (raw_questions if isinstance(raw_questions, list) else [])[:count]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)

        # 题库里已有同题就不重复入库：模型（尤其是 Mock 模板）很容易给出相同题干，
        # 不去重的话题库会被同一道题刷满，抽题也会出现重复。
        if db.exec(select(QuestionBank).where(QuestionBank.text == text)).first() is not None:
            continue

        tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()][:5]
        row = QuestionBank(
            text=text,
            type=str(item.get("type") or "training"),
            difficulty=_clamp_score(item.get("difficulty"), low=1, high=5, default=3),
            tags=tags or [str(topics[0])],
            reference_points=[str(point).strip() for point in (item.get("reference_points") or [])][:5],
            source="generated",
        )
        db.add(row)
        created.append(row)

    if created:
        db.commit()
        for row in created:
            db.refresh(row)
    return created


async def build_training_set(
    db: Session,
    *,
    llm: LLMClient,
    limit: int = 4,
    weak_count: int = 3,
    target_id: int | None = None,
    rng: random.Random | None = None,
) -> list[TrainingQuestion]:
    """组一套训练：默认 3 道薄弱点题 + 1 道随机题。"""
    random_source = rng or random.Random()
    weights = SelectorWeights()

    overview = mastery_overview(db)
    weak_items = overview[: max(1, weak_count)]
    mastery_by_topic = {int(item["topic_id"]): float(item["mastery"]) for item in overview}
    name_by_topic = {int(item["topic_id"]): str(item["name"]) for item in overview}
    weak_names = [str(item["name"]) for item in weak_items] or list(DEFAULT_WEAK_TOPICS)
    due_ids = due_topic_ids(db)

    target = db.get(JobTarget, target_id) if target_id else None
    target_label = ""
    if target is not None:
        target_label = " · ".join(part for part in (target.company, target.title) if part)
    jd_keywords = _jd_keywords(target.jd_text if target is not None else "")

    def matched_topics(tags: Sequence[str]) -> tuple[float, list[int], list[str]]:
        """题目标签命中的能力点：取掌握度最低的那个作为薄弱度依据。"""
        hits: list[tuple[float, int, str]] = []
        for tag in tags:
            for topic_id, name in name_by_topic.items():
                if _contains(tag, name):
                    hits.append((mastery_by_topic.get(topic_id, 0.0), topic_id, name))
        if not hits:
            return 0.0, [], []
        weakest = min(hits, key=lambda item: item[0])
        return weakest[0], [item[1] for item in hits], [item[2] for item in hits]

    candidates: list[Candidate] = []
    seen_texts: set[str] = set()
    for row in db.exec(select(QuestionBank)).all():
        if row.id is None:
            continue
        text = (row.text or "").strip()
        # 题库里可能有重复题干（历史数据、手动导入的都算），同一次训练不该出两道一样的题
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)

        tags = list(row.tags or [])
        mastery_value, topic_ids, _names = matched_topics(tags)
        candidates.append(
            Candidate(
                question_id=int(row.id),
                text=text,
                type=row.type or "general",
                difficulty=int(row.difficulty or 3),
                topic_ids=topic_ids,
                tags=tags,
                # 没有标签命中的题给一个基础薄弱度，否则它们永远没机会被练到
                weakness=(1.0 - mastery_value / MASTERY_MAX) if topic_ids else 0.3,
                jd_match=jd_match_score(jd_keywords, tags),
                due_urgency=1.0 if any(topic_id in due_ids for topic_id in topic_ids) else 0.0,
                origin="bank",
            )
        )

    covered_names = {
        name_by_topic[topic_id]
        for candidate in candidates
        for topic_id in candidate.topic_ids
        if topic_id in name_by_topic
    }
    missing_names = [name for name in weak_names if name not in covered_names]

    async def generate_and_attach(names: list[str], count: int) -> None:
        """生成题目并装配成候选（题库里没有覆盖到的能力点才走这里）。"""
        if count <= 0 or not names:
            return
        generated = await _generate_questions(
            db,
            llm=llm,
            topics=names,
            target_label=target_label,
            count=count,
        )
        for row in generated:
            if row.id is None:
                continue
            tags = list(row.tags or [])
            mastery_value, topic_ids, _names = matched_topics(tags)
            candidates.append(
                Candidate(
                    question_id=int(row.id),
                    text=row.text,
                    type=row.type or "training",
                    difficulty=int(row.difficulty or 3),
                    topic_ids=topic_ids,
                    tags=tags,
                    weakness=(1.0 - mastery_value / MASTERY_MAX) if topic_ids else 0.6,
                    jd_match=jd_match_score(jd_keywords, tags),
                    due_urgency=1.0 if any(topic_id in due_ids for topic_id in topic_ids) else 0.0,
                    origin="generated",
                )
            )

    await generate_and_attach(missing_names, min(len(missing_names), max(1, weak_count)))

    # 候选不够 limit 时补足：否则新用户（题库为空）只能拿到两三道题，
    # "3 道薄弱 + 1 道随机"这个承诺就不成立了。
    if len(candidates) < limit:
        await generate_and_attach(weak_names, limit - len(candidates))

    if not candidates:
        return []

    apply_type_penalty(candidates, _recent_training_types(db))

    picked = select_candidates(candidates, limit=min(weak_count, limit), weights=weights)
    picked_ids = {candidate.question_id for candidate in picked}

    results: list[TrainingQuestion] = [
        TrainingQuestion(
            question_id=candidate.question_id,
            text=candidate.text,
            type=candidate.type,
            difficulty=candidate.difficulty,
            tags=candidate.tags,
            topic_names=[name_by_topic[topic_id] for topic_id in candidate.topic_ids if topic_id in name_by_topic],
            origin=candidate.origin,
            kind="weak",
            reason=explain(candidate, weights),
        )
        for candidate in picked
    ]

    # 剩下的一两题用来"换换脑子"：随机抽，避免训练永远是同一批弱项
    remaining = [candidate for candidate in candidates if candidate.question_id not in picked_ids]
    while len(results) < limit and remaining:
        candidate = random_source.choice(remaining)
        remaining = [item for item in remaining if item.question_id != candidate.question_id]
        results.append(
            TrainingQuestion(
                question_id=candidate.question_id,
                text=candidate.text,
                type=candidate.type,
                difficulty=candidate.difficulty,
                tags=candidate.tags,
                topic_names=[name_by_topic[topic_id] for topic_id in candidate.topic_ids if topic_id in name_by_topic],
                origin=candidate.origin,
                kind="random",
                reason=explain(candidate, weights),
            )
        )

    return results


# --------------------------------------------------------------------------- #
# 批改
# --------------------------------------------------------------------------- #
async def grade_training_answer(
    db: Session,
    *,
    llm: LLMClient,
    question_id: int,
    answer: str,
) -> TrainingGrade:
    question = db.get(QuestionBank, question_id)
    if question is None:
        raise LookupError(f"题目不存在：{question_id}")

    content = (answer or "").strip()
    if not content:
        raise TrainingError("回答不能为空")

    messages = [
        LLMMessage(role="system", content=TRAINING_GRADE_PROMPT),
        LLMMessage(
            role="user",
            content=_marker_payload(
                {
                    "question": question.text,
                    "reference_points": list(question.reference_points or []),
                    "answer": content,
                }
            ),
        ),
    ]

    try:
        result = await llm.complete(task="training_grade", messages=messages, temperature=0.2, json_object=True)
    except LLMError as exc:
        log_llm_error(db, task="training_grade", provider=getattr(llm, "name", ""), error=str(exc))
        raise

    log_llm_result(db, result)

    try:
        data = extract_json(result.text)
    except ValueError:
        data = {}

    score = _clamp_score(data.get("score"))
    comment = str(data.get("comment") or "").strip()
    better_answer = str(data.get("better_answer") or "").strip()

    attempt = QuestionAttempt(
        question_id=question_id,
        answer_text=content,
        scores={"score": score, "comment": comment, "better_answer": better_answer},
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    changes: list[MasteryChange] = []
    for name in list(question.tags or [])[:MAX_TOPICS_PER_OBSERVATION]:
        topic = ensure_topic(db, str(name))
        if topic is None or topic.id is None:
            continue
        _row, event = apply_observation(
            db,
            topic_id=int(topic.id),
            score=float(score),
            source="training",
            item_id=attempt.id,
            note="训练批改",
        )
        changes.append(
            MasteryChange(
                topic_id=int(topic.id),
                name=topic.name,
                before=event.mastery_before,
                after=event.mastery_after,
            )
        )

    return TrainingGrade(
        question_id=question_id,
        attempt_id=attempt.id or 0,
        score=score,
        comment=comment,
        better_answer=better_answer,
        mastery=changes,
    )
