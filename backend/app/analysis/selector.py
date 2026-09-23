"""加权抽题：把"该练什么"变成一个**可解释**的分数。

为什么不用"按掌握度排序取最低的 N 个"：
- 那样会一直练同一批题，练到吐且覆盖不到岗位要求；
- 单选题型会让人形成应试惯性，真实面试反而更差。

所以这里是一个多目标加权和，并且提供 `explain()` 输出每一项的贡献——
前端可以把"为什么推荐这题"直接展示给用户（也让"低分题被优先命中"这条验收可验证）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(slots=True)
class SelectorWeights:
    weakness: float = 3.0  # 薄弱度：训练的价值就在于补弱，给最高权重
    jd_match: float = 1.2  # 与目标岗位 JD 的匹配度
    due_urgency: float = 1.5  # SM-2 到期紧迫度
    type_balance: float = 0.8  # 题型平衡（罚项：最近练过的题型降权）
    difficulty_fit: float = 0.5  # 难度适配


@dataclass(slots=True)
class Candidate:
    question_id: int
    text: str
    type: str = "general"
    difficulty: int = 3
    topic_ids: list[int] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # 下面这些是装配阶段算好的特征（都在 0–1）
    weakness: float = 0.0
    jd_match: float = 0.0
    due_urgency: float = 0.0
    type_penalty: float = 0.0
    # 来源标记：题库命中 / 为薄弱点新生成
    origin: str = "bank"


def difficulty_fit(difficulty: int, *, target: float = 3.0) -> float:
    """难度适配：离目标难度越远越低（0–1）。太难会挫败，太易没训练价值。"""
    return max(0.0, 1.0 - abs(float(difficulty) - target) / 2.0)


def jd_match_score(jd_keywords: Sequence[str], tags: Sequence[str]) -> float:
    """JD 关键词与题目标签的重合度（0–1）。

    用标签重合而不是把 JD 全文做向量检索：单用户场景下 JD 就一篇，
    关键词集合小而准，token 成本更低，而且结果可以解释（命中了哪几个词）。
    """
    if not jd_keywords or not tags:
        return 0.0

    normalized_tags = {tag.strip().lower() for tag in tags if tag and tag.strip()}
    if not normalized_tags:
        return 0.0

    hits = 0
    for keyword in jd_keywords:
        word = (keyword or "").strip().lower()
        if not word:
            continue
        # 双向包含：JD 里写"高并发"，题目标签写"并发"也应算命中
        if any(word in tag or tag in word for tag in normalized_tags):
            hits += 1

    return max(0.0, min(1.0, hits / len(jd_keywords)))


def score_candidate(candidate: Candidate, weights: SelectorWeights | None = None) -> float:
    config = weights or SelectorWeights()
    return (
        config.weakness * candidate.weakness
        + config.jd_match * candidate.jd_match
        + config.due_urgency * candidate.due_urgency
        + config.difficulty_fit * difficulty_fit(candidate.difficulty)
        - config.type_balance * candidate.type_penalty
    )


def explain(candidate: Candidate, weights: SelectorWeights | None = None) -> dict[str, float]:
    """每一项对总分的贡献，便于前端展示"为什么推荐这题"。"""
    config = weights or SelectorWeights()
    return {
        "weakness": round(config.weakness * candidate.weakness, 3),
        "jd_match": round(config.jd_match * candidate.jd_match, 3),
        "due_urgency": round(config.due_urgency * candidate.due_urgency, 3),
        "difficulty_fit": round(config.difficulty_fit * difficulty_fit(candidate.difficulty), 3),
        "type_penalty": round(-config.type_balance * candidate.type_penalty, 3),
        "total": round(score_candidate(candidate, config), 3),
    }


def rank(candidates: Sequence[Candidate], weights: SelectorWeights | None = None) -> list[Candidate]:
    """按加权得分降序排列（同分时用 question_id 保证结果稳定可复现）。"""
    return sorted(
        candidates,
        key=lambda item: (-score_candidate(item, weights), item.question_id),
    )


def select(candidates: Sequence[Candidate], *, limit: int, weights: SelectorWeights | None = None) -> list[Candidate]:
    if limit <= 0:
        return []
    return rank(candidates, weights)[:limit]


def apply_type_penalty(candidates: Sequence[Candidate], recently_used_types: Sequence[str]) -> None:
    """把"最近练过的题型"打折，就地修改候选（null 安全：不改返回值）。"""
    if not recently_used_types:
        return
    counts: dict[str, int] = {}
    for item_type in recently_used_types:
        counts[item_type] = counts.get(item_type, 0) + 1

    busiest = max(counts.values()) if counts else 0
    for candidate in candidates:
        used = counts.get(candidate.type, 0)
        candidate.type_penalty = round(used / busiest, 3) if busiest else 0.0
