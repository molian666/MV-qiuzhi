"""学习路径：薄弱度、岗位需求度、覆盖广度与优先级。

验收标准是「学习路径与薄弱点联动」——所以核心是：
**薄弱的东西真的会排在前面，而且样本不足时不会虚高**。
"""

from __future__ import annotations

import pytest

from app.analysis.learning_path import (
    MIN_ATTEMPTS,
    WEIGHT_BREADTH,
    WEIGHT_DEMAND,
    WEIGHT_WEAKNESS,
    GapInput,
    WeaknessInput,
    action_for,
    breadth_score,
    build_path,
    demand_score,
    estimate_hours,
    summarize_path,
    weakness_score,
)


# --------------------------------------------------------------------------- #
# 三个分量
# --------------------------------------------------------------------------- #
def test_weakness_combines_mastery_and_accuracy() -> None:
    weak = weakness_score(WeaknessInput(topic="Redis", mastery=0.2, accuracy=0.4, attempts=6))
    strong = weakness_score(WeaknessInput(topic="Redis", mastery=0.9, accuracy=0.9, attempts=6))

    assert weak > strong
    assert 0 <= strong <= 1
    assert 0 <= weak <= 1


def test_small_sample_is_discounted() -> None:
    """两次训练错一次不算"薄弱"——那可能只是陌生。"""
    few = weakness_score(WeaknessInput(topic="Redis", mastery=0.0, accuracy=0.0, attempts=MIN_ATTEMPTS - 1))
    enough = weakness_score(WeaknessInput(topic="Redis", mastery=0.0, accuracy=0.0, attempts=MIN_ATTEMPTS))

    assert few < enough
    assert few == pytest.approx(0.5, abs=0.01)


def test_demand_prefers_hard_requirements() -> None:
    hard = demand_score(GapInput(word="Python", weight=1.0, count=3), max_count=3)
    bonus = demand_score(GapInput(word="Kafka", weight=0.5, count=1), max_count=3)
    assert hard > bonus


def test_demand_counts_frequency() -> None:
    often = demand_score(GapInput(word="Python", weight=1.0, count=5), max_count=5)
    once = demand_score(GapInput(word="Python", weight=1.0, count=1), max_count=5)
    assert often > once


def test_breadth_finds_covered_gaps() -> None:
    gaps = [GapInput(word=word) for word in ("缓存", "限流", "分库分表", "Kafka")]
    score, covered = breadth_score("高并发", gaps)

    assert score > 0
    assert {"缓存", "限流", "分库分表"} <= set(covered)
    # 覆盖不到的缺口不该被算进去
    assert "Kafka" not in covered


def test_breadth_is_zero_without_relations() -> None:
    score, covered = breadth_score("某个没见过的词", [GapInput(word="Kafka")])
    assert score == 0.0
    assert covered == []


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #
def test_priority_uses_all_three_weights() -> None:
    rows = build_path(
        [WeaknessInput(topic="高并发", mastery=0.2, accuracy=0.3, attempts=8)],
        [GapInput(word="高并发", weight=1.0, count=3), GapInput(word="缓存", weight=0.7)],
    )
    row = rows[0]
    bd = row.breakdown
    expected = (
        WEIGHT_WEAKNESS * bd["weakness"] + WEIGHT_DEMAND * bd["demand"] + WEIGHT_BREADTH * bd["breadth"]
    )
    assert row.priority == pytest.approx(round(expected * 100, 1))
    # 明细要能解释这个排序，否则用户不同意时无从反驳
    assert set(bd) == {"weakness", "demand", "breadth"}


def test_weak_topic_ranks_above_unrelated_gap() -> None:
    rows = build_path(
        [WeaknessInput(topic="分布式", mastery=0.1, accuracy=0.2, attempts=10)],
        [GapInput(word="Kafka", weight=0.5, count=1)],
    )
    assert rows[0].topic == "分布式"
    assert rows[0].source == "weakness"


def test_topic_that_is_both_weak_and_gap_is_merged() -> None:
    """同一个主题既薄弱又是缺口时必须合并成一条，否则会看着像两件事。"""
    rows = build_path(
        [WeaknessInput(topic="缓存", mastery=0.3, accuracy=0.4, attempts=5)],
        [GapInput(word="缓存", weight=1.0, count=2)],
    )
    topics = [item.topic for item in rows]
    assert topics.count("缓存") == 1
    assert rows[0].source == "jd_gap"
    assert "岗位把它列为硬性要求" in rows[0].reason


def test_pure_gap_gets_lower_weight_than_a_weak_one() -> None:
    weak = build_path([WeaknessInput(topic="高并发", mastery=0.1, accuracy=0.1, attempts=10)], [])
    gap = build_path([], [GapInput(word="Kafka", weight=1.0, count=3)])
    assert weak[0].priority > gap[0].priority


def test_path_is_sorted_and_limited() -> None:
    weaknesses = [
        WeaknessInput(topic=f"主题{i}", mastery=0.1 * i, accuracy=0.1 * i, attempts=5) for i in range(1, 8)
    ]
    rows = build_path(weaknesses, [], limit=3)
    assert len(rows) == 3
    priorities = [item.priority for item in rows]
    assert priorities == sorted(priorities, reverse=True)


def test_reason_explains_small_samples() -> None:
    rows = build_path([WeaknessInput(topic="Redis", mastery=0.1, accuracy=0.0, attempts=1)], [])
    assert "样本少" in rows[0].reason


def test_every_suggestion_has_an_action() -> None:
    rows = build_path(
        [WeaknessInput(topic="高并发", mastery=0.2, accuracy=0.2, attempts=5)],
        [GapInput(word="某个没见过的词", weight=0.5)],
    )
    for row in rows:
        # 每条都要有具体做法，不能是"建议加强学习"这种废话
        assert len(row.action) > 15
        assert row.hours > 0


def test_action_specific_beats_generic() -> None:
    assert action_for("高并发") != action_for("某个没见过的词")
    assert "压测" in action_for("高并发")
    assert estimate_hours("高并发") > estimate_hours("某个没见过的词")


def test_empty_inputs() -> None:
    assert build_path([], []) == []
    assert summarize_path([])["count"] == 0


def test_summary_shape() -> None:
    rows = build_path(
        [WeaknessInput(topic="高并发", mastery=0.1, accuracy=0.1, attempts=6)],
        [GapInput(word="Kafka", weight=0.7)],
    )
    summary = summarize_path(rows)
    assert summary["count"] == len(rows)
    assert summary["hours"] > 0
    assert summary["top"] == rows[0].topic
    assert "共" in summary["note"]
