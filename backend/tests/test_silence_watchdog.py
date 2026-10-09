"""沉默看门狗。

这个功能的全部价值在于"**别让面试无限期卡住**"，所以测试重点是阈值边界与
"不许反复催"——而不是文案。

阈值这类代码的 bug 有个共同特征：只在真实时间流逝时才暴露。所以这里用纯数字把
每条边界钉死，不依赖任何真实等待。
"""

from __future__ import annotations

import pytest

from app.analysis.silence import (
    ACTION_ADVANCE,
    ACTION_NONE,
    ACTION_NUDGE,
    COLD_NUDGE_MESSAGE,
    MAX_NUDGES,
    TIMEOUT_MESSAGE,
    WARM_NUDGE_MESSAGE,
    SilencePolicy,
    decide_silence,
)

POLICY = SilencePolicy(
    nudge_ms=10_000,
    timeout_ms=20_000,
    cold_start_nudge_ms=15_000,
    cold_start_timeout_ms=30_000,
)


# --------------------------------------------------------------------------- #
# 说过话之后沉默
# --------------------------------------------------------------------------- #
def test_quiet_before_the_nudge_threshold_does_nothing() -> None:
    decision = decide_silence(silence_ms=5_000, has_spoken=True, policy=POLICY)

    assert decision.action == ACTION_NONE
    assert decision.should_act is False


def test_nudges_at_the_threshold() -> None:
    decision = decide_silence(silence_ms=10_000, has_spoken=True, policy=POLICY)

    assert decision.action == ACTION_NUDGE
    assert decision.message == WARM_NUDGE_MESSAGE


def test_one_millisecond_before_the_threshold_does_nothing() -> None:
    """边界必须精确到毫秒，不能靠"差不多"。"""
    assert decide_silence(silence_ms=9_999, has_spoken=True, policy=POLICY).action == ACTION_NONE


def test_times_out_at_the_timeout_threshold() -> None:
    decision = decide_silence(silence_ms=20_000, has_spoken=True, policy=POLICY)

    assert decision.action == ACTION_ADVANCE
    assert decision.message == TIMEOUT_MESSAGE


def test_timeout_beats_a_pending_nudge() -> None:
    """已经等太久了就**别再补一句"还在吗"**，直接过题。"""
    decision = decide_silence(silence_ms=25_000, has_spoken=True, nudges_sent=0, policy=POLICY)

    assert decision.action == ACTION_ADVANCE


# --------------------------------------------------------------------------- #
# 还没开口：更宽容
# --------------------------------------------------------------------------- #
def test_cold_start_uses_the_more_patient_thresholds() -> None:
    """同一时刻，"还没开口"不该被提醒，而"说过话后沉默"已经该提醒了。

    前者常见原因是设备/找简历，催了只会让人紧张。
    """
    assert decide_silence(silence_ms=12_000, has_spoken=True, policy=POLICY).action == ACTION_NUDGE
    assert decide_silence(silence_ms=12_000, has_spoken=False, policy=POLICY).action == ACTION_NONE


def test_cold_start_nudge_message_gives_a_step_down() -> None:
    decision = decide_silence(silence_ms=15_000, has_spoken=False, policy=POLICY)

    assert decision.action == ACTION_NUDGE
    assert decision.message == COLD_NUDGE_MESSAGE
    # 这一档要给台阶、不给压力
    assert "不着急" in decision.message


def test_cold_start_still_times_out() -> None:
    """宽容不等于永远等下去——否则"无限期卡住"这个原始问题没被解决。"""
    decision = decide_silence(silence_ms=30_000, has_spoken=False, policy=POLICY)

    assert decision.action == ACTION_ADVANCE


# --------------------------------------------------------------------------- #
# 不许反复催
# --------------------------------------------------------------------------- #
def test_does_not_nudge_twice() -> None:
    """连着问三遍"在吗"会让人烦躁，而且不像真人。"""
    decision = decide_silence(silence_ms=12_000, has_spoken=True, nudges_sent=1, policy=POLICY)

    assert decision.action == ACTION_NONE


def test_still_times_out_after_a_nudge() -> None:
    """催过一次之后仍然要能过题，否则就卡在"催过但没人应"的状态里。"""
    decision = decide_silence(silence_ms=20_000, has_spoken=True, nudges_sent=1, policy=POLICY)

    assert decision.action == ACTION_ADVANCE


def test_zero_max_nudges_goes_straight_to_timeout() -> None:
    policy = SilencePolicy(
        nudge_ms=1_000, timeout_ms=5_000, cold_start_nudge_ms=1_000, cold_start_timeout_ms=5_000, max_nudges=0
    )
    assert decide_silence(silence_ms=2_000, has_spoken=True, policy=policy).action == ACTION_NONE
    assert decide_silence(silence_ms=5_000, has_spoken=True, policy=policy).action == ACTION_ADVANCE


def test_negative_nudges_are_treated_as_zero() -> None:
    decision = decide_silence(silence_ms=12_000, has_spoken=True, nudges_sent=-5, policy=POLICY)
    assert decision.action == ACTION_NUDGE


# --------------------------------------------------------------------------- #
# 阈值自身的健全性
# --------------------------------------------------------------------------- #
def test_clamping_keeps_nudge_before_timeout() -> None:
    """**这条很重要**：提醒必须早于超时，否则提醒永远没机会出现。

    顺序写反时功能是"静默失效"的——不报错、什么都不发生，最难查。
    """
    broken = SilencePolicy(nudge_ms=60_000, timeout_ms=10_000).clamped()

    assert broken.nudge_ms < broken.timeout_ms


def test_clamping_keeps_cold_start_ordered_too() -> None:
    broken = SilencePolicy(cold_start_nudge_ms=90_000, cold_start_timeout_ms=20_000).clamped()
    assert broken.cold_start_nudge_ms < broken.cold_start_timeout_ms


def test_clamping_rejects_absurdly_small_values() -> None:
    tiny = SilencePolicy(nudge_ms=0, timeout_ms=0, cold_start_nudge_ms=0, cold_start_timeout_ms=0).clamped()

    assert tiny.nudge_ms >= 1_000
    assert tiny.timeout_ms > tiny.nudge_ms


def test_default_policy_is_patient_but_finite() -> None:
    """默认值要像真人：不会 5 秒没人应就跳过你，也不会等上几分钟。"""
    policy = SilencePolicy()

    assert policy.nudge_ms >= 10_000
    assert policy.timeout_ms <= 120_000


def test_negative_silence_is_clamped_to_zero() -> None:
    """时钟回拨不该被判成"沉默了很久"。"""
    decision = decide_silence(silence_ms=-5_000, has_spoken=True, policy=POLICY)

    assert decision.action == ACTION_NONE
    assert decision.elapsed_ms == 0


def test_max_nudges_default_is_one() -> None:
    assert MAX_NUDGES == 1
