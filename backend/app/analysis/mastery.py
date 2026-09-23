"""掌握度模型（纯函数）。

两个刻意的取舍：

1. **用简单的指数滑动更新，而不是 BKT / Elo**。单用户、证据极少（一场面试才几条），
   复杂模型的参数根本估不出来，只会给出"看起来很科学但不可解释"的数字。
   这里的每一步都能手算复现：`new = old + k × (目标 - old)`。
2. **每次变化都留 mastery_event**。用户要能看到"这个标签为什么变成 62 分"，
   而不是一个黑箱数字——这也是 M5 验收里"掌握度随练习变化**可解释**"的落地方式。
"""

from __future__ import annotations

from collections.abc import Sequence

UPDATE_K = 0.3
MASTERY_MIN = 0.0
MASTERY_MAX = 100.0
# 掌握度低于这个值直接判高危
HIGH_RISK_MASTERY = 40.0
# 或者最近连续 N 次都在低分区
HIGH_RISK_CONSECUTIVE = 2


def score_to_norm(score: float, *, low: float = 1.0, high: float = 5.0) -> float:
    """把 1–5 分的评分映射到 0–1 的"掌握比例"。"""
    if high <= low:
        return 0.0
    ratio = (float(score) - low) / (high - low)
    return max(0.0, min(1.0, ratio))


def target_mastery(score: float) -> float:
    """一次得分对应的目标掌握度（0–100）。"""
    return round(score_to_norm(score) * MASTERY_MAX, 2)


def update_mastery(current: float | None, score: float, *, k: float = UPDATE_K) -> float:
    """一次观察之后的新掌握度。

    `current is None` 表示这个标签第一次出现——直接用目标值，不做插值
    （否则首条证据的权重会被 0 起点的历史稀释，用户会觉得"练了没反应"）。
    """
    target = target_mastery(score)
    if current is None:
        return target

    value = float(current) + k * (target - float(current))
    return round(max(MASTERY_MIN, min(MASTERY_MAX, value)), 2)


def is_high_risk(*, mastery: float, recent_scores: Sequence[float] = ()) -> bool:
    """高危薄弱点：掌握度本身已很低，或者最近连续几次都答得差。"""
    if mastery < HIGH_RISK_MASTERY:
        return True
    if len(recent_scores) < HIGH_RISK_CONSECUTIVE:
        return False
    recent = list(recent_scores)[-HIGH_RISK_CONSECUTIVE:]
    return all(score_to_norm(score) <= 0.5 for score in recent)


def weakness(score_or_mastery: float, *, is_mastery: bool) -> float:
    """抽题用的"薄弱度"，落在 0–1（越大越该被优先练）。

    统一入口是为了让"掌握度"和"单次得分"两种输入都能喂给抽题器——
    题库里的题可能还没有任何掌握度记录，就退回用难度当薄弱度。
    """
    if is_mastery:
        return max(0.0, min(1.0, 1.0 - float(score_or_mastery) / MASTERY_MAX))
    return max(0.0, min(1.0, 1.0 - score_to_norm(score_or_mastery)))
