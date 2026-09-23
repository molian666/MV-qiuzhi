"""SM-2 排程的测试。

这几条断言定义了"复习节奏"：答对逐次拉长、答错回到 1 天、ease 有地板与上限。
把它测住，才能保证"该复习的题"真的会被排到前面。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.analysis.review import (
    DEFAULT_EASE,
    MAX_INTERVAL,
    MIN_EASE,
    next_schedule,
    quality_from_score,
    urgency,
)

NOW = datetime(2026, 1, 1, 12, 0, 0)


def test_quality_from_score_uses_ceil_so_three_is_passing() -> None:
    assert quality_from_score(1) == 0
    assert quality_from_score(2) == 2
    assert quality_from_score(3) == 3  # 刚好及格（SM-2 里 >= 3 算通过）
    assert quality_from_score(4) == 4
    assert quality_from_score(5) == 5


def test_failed_review_resets_to_one_day() -> None:
    schedule = next_schedule(quality=1, interval_days=20.0, ease=DEFAULT_EASE, repetitions=5, now=NOW)
    assert schedule.passed is False
    assert schedule.interval_days == 1.0
    assert schedule.repetitions == 0
    assert schedule.ease == 2.3  # 略微下调
    assert schedule.due_at == NOW + timedelta(days=1)


def test_first_pass_is_one_day() -> None:
    schedule = next_schedule(quality=5, repetitions=0, now=NOW)
    assert schedule.passed is True
    assert schedule.interval_days == 1.0
    assert schedule.repetitions == 1
    assert schedule.due_at == NOW + timedelta(days=1)


def test_second_pass_is_six_days() -> None:
    schedule = next_schedule(quality=5, interval_days=1.0, repetitions=1, now=NOW)
    assert schedule.interval_days == 6.0
    assert schedule.repetitions == 2


def test_third_pass_multiplies_by_ease() -> None:
    schedule = next_schedule(quality=5, interval_days=6.0, ease=2.5, repetitions=2, now=NOW)
    assert schedule.interval_days == 15.0  # 6 × 2.5
    assert schedule.ease > 2.5  # 满分会给 ease 加分
    assert schedule.due_at == NOW + timedelta(days=15)


def test_ease_has_floor() -> None:
    schedule = next_schedule(quality=0, ease=MIN_EASE, repetitions=3, now=NOW)
    assert schedule.ease == MIN_EASE


def test_interval_is_capped() -> None:
    schedule = next_schedule(quality=5, interval_days=300.0, ease=2.5, repetitions=6, now=NOW)
    assert schedule.interval_days <= MAX_INTERVAL


def test_urgency_prefers_overdue() -> None:
    assert urgency(None, now=NOW) == 0.0
    assert urgency(NOW - timedelta(days=1), now=NOW) == 1.0  # 已过期
    assert urgency(NOW + timedelta(days=10), now=NOW) == 0.0  # 超出视界
    middle = urgency(NOW + timedelta(days=3), now=NOW)
    assert 0.0 < middle < 1.0
