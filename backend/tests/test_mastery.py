"""掌握度模型的测试。

这些断言把"可解释"钉死：给定历史与本次得分，新掌握度必须能算出来，
否则用户看到数字变化却不知道为什么，训练就变成了玄学。
"""

from __future__ import annotations

import pytest

from app.analysis.mastery import (
    MASTERY_MAX,
    is_high_risk,
    score_to_norm,
    target_mastery,
    update_mastery,
    weakness,
)


def test_score_to_norm_maps_and_clamps() -> None:
    assert score_to_norm(1) == 0.0
    assert score_to_norm(3) == 0.5
    assert score_to_norm(5) == 1.0
    assert score_to_norm(0) == 0.0
    assert score_to_norm(9) == 1.0


def test_target_mastery_is_percentage() -> None:
    assert target_mastery(5) == MASTERY_MAX
    assert target_mastery(1) == 0.0
    assert target_mastery(3) == 50.0
    assert target_mastery(4) == 75.0


def test_first_observation_takes_target_directly() -> None:
    """首条证据直接用目标值。

    否则会被"0 起点"的历史稀释：练了一场却只涨 30 分，用户会觉得系统没反应。
    """
    assert update_mastery(None, 4) == 75.0
    assert update_mastery(None, 1) == 0.0


@pytest.mark.parametrize(
    ("current", "score", "expected"),
    [
        (50.0, 5, 65.0),  # 50 + 0.3 × (100 - 50)
        (50.0, 1, 35.0),  # 50 + 0.3 × (0 - 50)
        (50.0, 3, 50.0),  # 目标就是 50，不动
        (75.0, 5, 82.5),  # 75 + 0.3 × 25
    ],
)
def test_update_mastery_is_hand_computable(current: float, score: float, expected: float) -> None:
    assert update_mastery(current, score) == expected


def test_update_mastery_stays_in_range() -> None:
    for current in (0.0, 50.0, 100.0):
        for score in (1, 3, 5):
            value = update_mastery(current, score)
            assert 0.0 <= value <= MASTERY_MAX


def test_high_risk_by_low_mastery() -> None:
    assert is_high_risk(mastery=30.0) is True
    assert is_high_risk(mastery=40.0) is False


def test_high_risk_by_consecutive_low_scores() -> None:
    assert is_high_risk(mastery=70.0, recent_scores=[2, 2]) is True
    assert is_high_risk(mastery=70.0, recent_scores=[2]) is False  # 次数不够
    assert is_high_risk(mastery=70.0, recent_scores=[4, 4]) is False
    assert is_high_risk(mastery=70.0, recent_scores=[5, 2, 2]) is True  # 只看最近两次


def test_weakness_accepts_mastery_or_score() -> None:
    assert weakness(0.0, is_mastery=True) == 1.0
    assert weakness(100.0, is_mastery=True) == 0.0
    assert weakness(50.0, is_mastery=True) == 0.5
    assert weakness(1, is_mastery=False) == 1.0
    assert weakness(5, is_mastery=False) == 0.0
    assert weakness(3, is_mastery=False) == 0.5
