"""多账号的存取与选号装配。

`profiles.py` 的判定逻辑已有单独测试；这里管的是"从库里取状态"这一段是否忠实——
尤其是**今日用量按账号记账**：如果它永远读到 0，"额度已用完"就永远不成立，
多账号轮换也就只剩个空壳。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.apply.profile_store import (
    DEFAULT_PROFILE_DAILY_LIMIT,
    bump_profile_usage,
    choose_profile,
    create_profile,
    list_profiles,
    load_meta,
    mark_profile_used,
    profile_snapshot,
    profile_usage_today,
    set_profile_meta,
    trip_profile,
)
from app.db import session_scope

#: 模型用的是 `UTCDateTime`，**拒绝 naive datetime**（这是项目里踩过多次的坑）。
#: 所以测试里的时间一律带 tzinfo。
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 建账号
# --------------------------------------------------------------------------- #
def test_creates_a_profile_with_an_independent_browser_dir(client: Any) -> None:
    """**每个账号必须有自己的浏览器目录**：共用等于共用一个登录态，多账号就是假的。"""
    with session_scope() as db:
        first = create_profile(db, label="主号")
        second = create_profile(db, label="小号")

    assert first.user_data_dir
    assert second.user_data_dir
    assert first.user_data_dir != second.user_data_dir


def test_explicit_user_data_dir_is_respected(client: Any) -> None:
    with session_scope() as db:
        profile = create_profile(db, user_data_dir="my/custom/dir")
    assert profile.user_data_dir == "my/custom/dir"


def test_default_daily_limit_is_conservative(client: Any) -> None:
    """单账号限额要比手动模式（30）更保守：多账号不等于平台允许更多总量。"""
    assert DEFAULT_PROFILE_DAILY_LIMIT < 30


def test_lists_profiles_per_platform(client: Any) -> None:
    with session_scope() as db:
        create_profile(db, platform="boss", label="B")
        create_profile(db, platform="liepin", label="L")
        bosses = list_profiles(db, platform="boss")
        all_rows = list_profiles(db)

    assert [item.label for item in bosses] == ["B"]
    assert len(all_rows) == 2


# --------------------------------------------------------------------------- #
# 代理与语言
# --------------------------------------------------------------------------- #
def test_meta_round_trip(client: Any) -> None:
    with session_scope() as db:
        profile = create_profile(db, proxy="http://h:1", locale="en-US")
        meta = load_meta(db)[str(profile.id)]

    assert meta.proxy == "http://h:1"
    assert meta.locale == "en-US"


def test_set_profile_meta_updates_existing(client: Any) -> None:
    with session_scope() as db:
        profile = create_profile(db)
        set_profile_meta(db, int(profile.id or 0), proxy="socks5://h:2", locale="zh-CN")
        meta = load_meta(db)[str(profile.id)]

    assert meta.proxy == "socks5://h:2"


# --------------------------------------------------------------------------- #
# 快照与用量
# --------------------------------------------------------------------------- #
def test_snapshot_reports_todays_usage(client: Any) -> None:
    """**这条是关键**：用量永远读到 0 的话，"额度已用完"就永远不成立。"""
    with session_scope() as db:
        profile = create_profile(db, daily_limit=5)
        profile_id = int(profile.id or 0)
        bump_profile_usage(db, profile_id)
        bump_profile_usage(db, profile_id)

        snapshot = profile_snapshot(db, profile)

    assert snapshot.daily_sent == 2
    assert snapshot.remaining == 3


def test_usage_is_tracked_per_profile(client: Any) -> None:
    with session_scope() as db:
        first = create_profile(db)
        second = create_profile(db)
        bump_profile_usage(db, int(first.id or 0))
        bump_profile_usage(db, int(first.id or 0))
        bump_profile_usage(db, int(second.id or 0))

        assert profile_usage_today(db, int(first.id or 0)) == 2
        assert profile_usage_today(db, int(second.id or 0)) == 1


def test_usage_unknown_profile_is_zero(client: Any) -> None:
    with session_scope() as db:
        create_profile(db)
        assert profile_usage_today(db, 99999) == 0


def test_exhausted_profile_drops_out_of_selection(client: Any) -> None:
    with session_scope() as db:
        profile = create_profile(db, daily_limit=2)
        profile_id = int(profile.id or 0)
        bump_profile_usage(db, profile_id)
        bump_profile_usage(db, profile_id)

        choice = choose_profile(db, platform="boss", now=NOW)

    assert choice.profile is None
    assert "额度" in choice.reason


def test_snapshot_marks_unusable_proxy(client: Any) -> None:
    with session_scope() as db:
        profile = create_profile(db, proxy="not-a-proxy")
        snapshot = profile_snapshot(db, profile)

    assert snapshot.proxy_ok is False


def test_snapshot_treats_no_proxy_as_direct(client: Any) -> None:
    """没配代理＝直连，是可用的——不能因为"没配"就把账号判死。"""
    with session_scope() as db:
        profile = create_profile(db)
        snapshot = profile_snapshot(db, profile)

    assert snapshot.proxy_ok is True


# --------------------------------------------------------------------------- #
# 选号
# --------------------------------------------------------------------------- #
def test_no_profiles_falls_back_to_default_account(client: Any) -> None:
    """**多账号是可选能力**：一个都没配时不该报错，而是回落到默认账号。"""
    with session_scope() as db:
        choice = choose_profile(db, platform="boss", now=NOW)

    assert choice.profile is None
    assert "默认账号" in choice.reason


def test_choose_returns_proxy_and_fingerprint(client: Any) -> None:
    with session_scope() as db:
        create_profile(db, label="主号", proxy="http://h:1", locale="en-US")
        choice = choose_profile(db, platform="boss", now=NOW)

    assert choice.profile is not None
    assert choice.proxy == "http://h:1"
    assert choice.fingerprint is not None
    assert choice.fingerprint.locale == "en-US"
    assert choice.fingerprint.timezone_id == "America/New_York"


def test_choose_skips_a_tripped_profile(client: Any) -> None:
    """A 号被熔断时选到 B 号——这就是多账号的意义。"""
    with session_scope() as db:
        first = create_profile(db, label="A")
        create_profile(db, label="B")
        trip_profile(db, int(first.id or 0), until=NOW + timedelta(hours=3))

        choice = choose_profile(db, platform="boss", now=NOW)

    assert choice.profile is not None
    assert choice.profile.label == "B"


def test_choose_reports_when_every_profile_is_out(client: Any) -> None:
    with session_scope() as db:
        create_profile(db, label="A")
        create_profile(db, label="B")
        for row in list_profiles(db):
            trip_profile(db, int(row.id or 0), until=NOW + timedelta(hours=3))

        choice = choose_profile(db, platform="boss", now=NOW)

    assert choice.profile is None
    assert "熔断" in choice.reason


def test_choose_warns_about_a_localhost_proxy(client: Any) -> None:
    """指向本机的代理不会改变出口 IP——要提醒，否则用户以为换好了。"""
    with session_scope() as db:
        create_profile(db, proxy="http://127.0.0.1:7890")
        choice = choose_profile(db, platform="boss", now=NOW)

    assert any("出口 IP 不会改变" in item for item in choice.warnings)


def test_mark_used_feeds_the_switch_decision(client: Any) -> None:
    """用过之后要被记下来，否则选号会每次挑同一个"剩余最多"的号。"""
    with session_scope() as db:
        create_profile(db, label="A")
        create_profile(db, label="B")
        rows = list_profiles(db)
        target = int(rows[1].id or 0)

        mark_profile_used(db, target, at=NOW - timedelta(seconds=10))
        refreshed = [row for row in list_profiles(db) if int(row.id or 0) == target][0]

    assert refreshed.last_used_at is not None

