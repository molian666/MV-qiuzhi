"""多账号选号逻辑。

这里的代价与自动拉黑类似但方向不同：**选错账号不会丢机会，会让账号被封**。
所以测试重点是"不该投的时候一个都不选"——尤其是"第一个账号刚被熔断"这种情形。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.apply.profiles import (
    DEFAULT_SWITCH_RATIO,
    ProfileSnapshot,
    pick_profile,
    usable_profiles,
)

NOW = datetime(2026, 9, 21, 12, 0)


def _profile(profile_id: int, **kwargs: object) -> ProfileSnapshot:
    base: dict[str, object] = {
        "profile_id": profile_id,
        "label": f"账号{profile_id}",
        "enabled": True,
        "daily_sent": 0,
        "daily_limit": 30,
        "circuit_broken_until": None,
        "proxy_ok": True,
    }
    base.update(kwargs)
    return ProfileSnapshot(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 基本选择
# --------------------------------------------------------------------------- #
def test_picks_a_profile_with_quota() -> None:
    choice = pick_profile([_profile(1)], now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 1
    assert "剩余" in choice.reason


def test_no_profiles_configured() -> None:
    choice = pick_profile([], now=NOW)

    assert choice.profile is None
    assert "还没有配置" in choice.reason


def test_disabled_profile_is_not_used() -> None:
    choice = pick_profile([_profile(1, enabled=False)], now=NOW)

    assert choice.profile is None
    assert "停用" in choice.reason


def test_exhausted_profile_is_not_used() -> None:
    choice = pick_profile([_profile(1, daily_sent=30, daily_limit=30)], now=NOW)

    assert choice.profile is None
    assert "额度已用完" in choice.reason


# --------------------------------------------------------------------------- #
# 熔断：按账号独立，这是多账号的核心价值
# --------------------------------------------------------------------------- #
def test_broken_profile_is_not_used() -> None:
    choice = pick_profile([_profile(1, circuit_broken_until=NOW + timedelta(hours=3))], now=NOW)

    assert choice.profile is None
    assert "熔断" in choice.reason


def test_expired_circuit_is_usable_again() -> None:
    choice = pick_profile([_profile(1, circuit_broken_until=NOW - timedelta(minutes=1))], now=NOW)

    assert choice.profile is not None


def test_one_broken_profile_does_not_block_the_other() -> None:
    """**这就是多账号的意义**：A 号被风控不该让 B 号也停。"""
    profiles = [
        _profile(1, circuit_broken_until=NOW + timedelta(hours=3)),
        _profile(2),
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


def test_exhausted_profile_falls_through_to_the_next() -> None:
    profiles = [_profile(1, daily_sent=30), _profile(2, daily_sent=5)]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


# --------------------------------------------------------------------------- #
# 代理不可用 = 账号不可用（别裸奔）
# --------------------------------------------------------------------------- #
def test_profile_with_broken_proxy_is_not_used() -> None:
    """**关键**：代理配错会让请求走真实 IP，比不用代理更糟。"""
    choice = pick_profile([_profile(1, proxy_ok=False)], now=NOW)

    assert choice.profile is None
    assert "代理" in choice.reason


def test_broken_proxy_on_one_falls_through_to_another() -> None:
    profiles = [_profile(1, proxy_ok=False), _profile(2)]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


# --------------------------------------------------------------------------- #
# 切换时机：用到接近限额才换，不轮流平均分
# --------------------------------------------------------------------------- #
def test_keeps_using_the_same_profile_below_the_switch_ratio() -> None:
    """低于切换阈值时继续用最近那个，而不是跳到另一个。

    轮流平均分会把每个账号都变成"半活跃"，行为更不像真人，额度也永远用不满。
    """
    profiles = [
        _profile(1, daily_sent=5, daily_limit=30, last_used_at=NOW - timedelta(minutes=1)),
        _profile(2, daily_sent=0, daily_limit=30, last_used_at=NOW - timedelta(days=2)),
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 1, "还没到切换阈值，应该继续用 1 号"


def test_recently_used_wins_over_more_remaining_below_the_threshold() -> None:
    """**这条是防"每投一条都换号"的关键**。

    只按剩余额度排的话，`daily_sent=0` 的号永远赢，于是每个账号都被轮流用一点点——
    那正是最不像真人的用法。
    """
    profiles = [
        _profile(1, daily_sent=8, daily_limit=30, last_used_at=NOW - timedelta(seconds=30)),
        _profile(2, daily_sent=0, daily_limit=30, last_used_at=NOW - timedelta(days=5)),
    ]
    usable = usable_profiles(profiles, now=NOW)

    assert [item.profile_id for item in usable] == [1, 2]


def test_broken_profile_is_skipped_even_if_most_recent() -> None:
    profiles = [
        _profile(1, last_used_at=NOW - timedelta(seconds=5), circuit_broken_until=NOW + timedelta(hours=2)),
        _profile(2, last_used_at=NOW - timedelta(days=1)),
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


def test_switches_away_once_past_the_switch_ratio() -> None:
    """到了阈值就换下一个，留出余量而不是正好撞上限。"""
    profiles = [
        _profile(1, daily_sent=29, daily_limit=30),
        _profile(2, daily_sent=0, daily_limit=30),
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


def test_switch_ratio_boundary() -> None:
    """恰好等于阈值时算"该换了"——边界必须明确。"""
    limit = 10
    at_ratio = int(limit * DEFAULT_SWITCH_RATIO)
    profiles = [
        _profile(1, daily_sent=at_ratio, daily_limit=limit),
        _profile(2, daily_sent=0, daily_limit=limit),
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


def test_custom_switch_ratio_is_respected() -> None:
    profiles = [
        _profile(1, daily_sent=5, daily_limit=10),
        _profile(2, daily_sent=0, daily_limit=10),
    ]
    # 阈值 0.3 时 5/10 已经超过，应当换
    choice = pick_profile(profiles, now=NOW, switch_ratio=0.3)
    assert choice.profile is not None
    assert choice.profile.profile_id == 2


def test_picks_the_one_with_most_remaining_when_both_need_switching() -> None:
    profiles = [
        _profile(1, daily_sent=29, daily_limit=30),  # 剩 1
        _profile(2, daily_sent=25, daily_limit=30),  # 剩 5
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is not None
    assert choice.profile.profile_id == 2


# --------------------------------------------------------------------------- #
# 可用列表
# --------------------------------------------------------------------------- #
def test_usable_profiles_excludes_unusable_ones() -> None:
    profiles = [
        _profile(1, enabled=False),
        _profile(2, daily_sent=30),
        _profile(3, circuit_broken_until=NOW + timedelta(hours=1)),
        _profile(4, proxy_ok=False),
        _profile(5),
    ]
    usable = usable_profiles(profiles, now=NOW)

    assert [item.profile_id for item in usable] == [5]


def test_usable_profiles_is_empty_when_everything_is_out() -> None:
    assert usable_profiles([], now=NOW) == []
    assert usable_profiles([_profile(1, enabled=False)], now=NOW) == []


def test_every_unusable_reason_is_reported_together() -> None:
    """一个都不能用时，原因要**都说出来**——用户才知道该去修什么。"""
    profiles = [
        _profile(1, circuit_broken_until=NOW + timedelta(hours=1)),
        _profile(2, daily_sent=30),
        _profile(3, proxy_ok=False),
    ]
    choice = pick_profile(profiles, now=NOW)

    assert choice.profile is None
    assert "熔断" in choice.reason
    assert "额度" in choice.reason
    assert "代理" in choice.reason


def test_remaining_never_goes_negative() -> None:
    """透支过的账号 remaining 不能是负数（否则排序会把它当成额度最多的）。"""
    item = _profile(1, daily_sent=50, daily_limit=30)
    assert item.remaining == 0
