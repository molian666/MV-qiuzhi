"""限速器与熔断器。

这两个模块存在的唯一目的是"别把账号投废"，所以边界必须钉死：
一分钟内第 N+1 次、当日第 M+1 次、验证码之后不许继续投。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.apply.breaker import (
    BreakerSignal,
    circuit_until,
    describe,
    is_broken,
    should_trip,
)
from app.apply.throttler import (
    MAX_PER_DAY,
    MAX_PER_MINUTE,
    ThrottleConfig,
    ThrottleState,
    can_send,
    next_delay_seconds,
    record_send,
    remaining_quota,
)

NOW = datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 限速
# --------------------------------------------------------------------------- #
def test_allows_until_per_minute_limit() -> None:
    config = ThrottleConfig(per_minute=3, per_day=30)
    state = ThrottleState()
    for _ in range(3):
        assert can_send(state, config, now=NOW).allowed is True
        record_send(state, now=NOW)

    decision = can_send(state, config, now=NOW)
    assert decision.allowed is False
    assert "本分钟" in decision.reason
    assert decision.wait_seconds > 0


def test_minute_window_slides() -> None:
    config = ThrottleConfig(per_minute=2, per_day=30)
    state = ThrottleState()
    record_send(state, now=NOW)
    record_send(state, now=NOW)
    assert can_send(state, config, now=NOW).allowed is False

    later = NOW + timedelta(seconds=61)
    assert can_send(state, config, now=later).allowed is True


def test_per_day_limit_blocks_first() -> None:
    config = ThrottleConfig(per_minute=3, per_day=5)
    state = ThrottleState(sent_today=5)
    decision = can_send(state, config, now=NOW)
    assert decision.allowed is False
    assert "今日已达上限" in decision.reason


def test_config_is_clamped_to_safe_ceiling() -> None:
    # UI 上把数字填得再大，也不能突破硬上限
    config = ThrottleConfig(per_minute=999, per_day=9999, jitter_min_seconds=-1, jitter_max_seconds=-5).clamped()
    assert config.per_minute == MAX_PER_MINUTE
    assert config.per_day == MAX_PER_DAY
    assert config.jitter_min_seconds >= 0.0
    assert config.jitter_max_seconds >= config.jitter_min_seconds


def test_config_never_sets_daily_below_per_minute() -> None:
    config = ThrottleConfig(per_minute=30, per_day=5).clamped()
    assert config.per_day >= config.per_minute


def test_jitter_stays_within_range() -> None:
    config = ThrottleConfig(jitter_min_seconds=3.0, jitter_max_seconds=9.0)
    rng = random.Random(7)
    values = [next_delay_seconds(config, rng=rng) for _ in range(50)]
    assert all(3.0 <= value <= 9.0 for value in values)
    assert len(set(values)) > 1  # 不是固定间隔（固定间隔本身就是机器特征）


def test_record_send_counts_toward_both_limits() -> None:
    state = ThrottleState()
    record_send(state, now=NOW)
    assert state.sent_today == 1
    assert len(state.recent_sends) == 1


def test_remaining_quota() -> None:
    config = ThrottleConfig(per_day=30)
    assert remaining_quota(ThrottleState(sent_today=4), config) == 26
    assert remaining_quota(ThrottleState(sent_today=99), config) == 0


# --------------------------------------------------------------------------- #
# 熔断
# --------------------------------------------------------------------------- #
def test_risk_signals_trip_the_breaker() -> None:
    for kind in ("login_lost", "captcha", "rate_limited"):
        assert should_trip(BreakerSignal(kind=kind)) is True


def test_page_changed_does_not_trip() -> None:
    # 页面改版是我们的适配问题，不该惩罚用户的账号
    assert should_trip(BreakerSignal(kind="page_changed")) is False
    assert should_trip(BreakerSignal(kind="unknown")) is False
    assert should_trip(None) is False


def test_circuit_lasts_until_end_of_day() -> None:
    until = circuit_until(NOW)
    assert until.hour == 23 and until.minute == 59
    assert until.date() == NOW.date()


def test_circuit_until_is_always_in_the_future() -> None:
    late = datetime(2026, 5, 20, 23, 59, 59, 500_000, tzinfo=timezone.utc)
    assert circuit_until(late) > late


def test_is_broken() -> None:
    until = NOW + timedelta(hours=2)
    assert is_broken(until, now=NOW) is True
    assert is_broken(NOW - timedelta(seconds=1), now=NOW) is False
    assert is_broken(None, now=NOW) is False


def test_describe_tells_user_what_to_do() -> None:
    assert "重新扫码" in describe(BreakerSignal(kind="login_lost"))
    assert "手动完成验证" in describe(BreakerSignal(kind="captcha"))
    assert "校准选择器" in describe(BreakerSignal(kind="page_changed"))
    assert "停投" in describe(BreakerSignal(kind="rate_limited", detail="操作过于频繁"))
