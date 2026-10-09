"""投递排期的时间计算。

调度代码的 bug 几乎全长在边界上：跨天、恰好等于设定时刻、重启后错过了时间点。
这个文件就是来钉这些边界的——所以里面大量是"差一分钟"的用例。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.apply.schedule import (
    DEFAULT_GRACE_MINUTES,
    MAX_SLOTS_PER_DAY,
    DailySchedule,
    describe_schedule,
    due_run_at,
    format_hhmm,
    next_run_at,
    parse_hhmm,
    parse_schedule,
)

NINE = time(9, 0)
NINE_THIRTY = time(9, 30)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def test_parse_hhmm_accepts_valid_values() -> None:
    assert parse_hhmm("09:30") == time(9, 30)
    assert parse_hhmm("0:05") == time(0, 5)
    assert parse_hhmm("23:59") == time(23, 59)
    assert parse_hhmm(" 8:00 ") == time(8, 0)


def test_parse_hhmm_rejects_garbage() -> None:
    """认不出来就返回 None，让调用方拒绝——**不要猜**一个时间出来。"""
    for bad in ("", "   ", "9", "09:60", "24:00", "-1:00", "abc", "09:30:00", "9-30"):
        assert parse_hhmm(bad) is None, bad


def test_parse_schedule_sorts_and_dedupes() -> None:
    assert parse_schedule(["18:00", "09:30", "09:30", "08:00"]) == (
        time(8, 0),
        time(9, 30),
        time(18, 0),
    )


def test_parse_schedule_drops_unparseable_but_keeps_the_rest() -> None:
    assert parse_schedule(["09:00", "garbage", "", "10:00"]) == (NINE, time(10, 0))


def test_parse_schedule_of_only_garbage_yields_nothing() -> None:
    """一个都认不出来时不能"猜一个时间"——返回空，于是不会自动跑。"""
    assert parse_schedule(["nonsense"]) == ()
    assert parse_schedule(None) == ()


def test_parse_schedule_caps_the_number_of_slots() -> None:
    many = [f"{hour:02d}:00" for hour in range(24)]
    assert len(parse_schedule(many)) == MAX_SLOTS_PER_DAY


def test_format_hhmm_pads_to_two_digits() -> None:
    assert format_hhmm(time(9, 5)) == "09:05"
    assert format_hhmm(time(18, 30)) == "18:30"


# --------------------------------------------------------------------------- #
# next_run_at
# --------------------------------------------------------------------------- #
def test_next_run_is_today_when_still_ahead() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 8, 0)

    assert next_run_at(schedule, now=now) == datetime(2026, 9, 21, 9, 0)


def test_next_run_rolls_to_tomorrow_after_the_last_slot() -> None:
    schedule = DailySchedule(times=(NINE, time(18, 0)), enabled=True)
    now = datetime(2026, 9, 21, 20, 0)

    assert next_run_at(schedule, now=now) == datetime(2026, 9, 22, 9, 0)


def test_next_run_skips_to_the_later_slot_within_the_same_day() -> None:
    schedule = DailySchedule(times=(NINE, time(18, 0)), enabled=True)
    now = datetime(2026, 9, 21, 10, 0)

    assert next_run_at(schedule, now=now) == datetime(2026, 9, 21, 18, 0)


def test_next_run_exactly_at_the_slot_is_not_the_next_run() -> None:
    """恰好等于设定时刻时，"下一个"必须是明天——否则会在同一分钟反复触发。"""
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, 0)

    assert next_run_at(schedule, now=now) == datetime(2026, 9, 22, 9, 0)


def test_next_run_is_none_without_a_schedule() -> None:
    now = datetime(2026, 9, 21, 8, 0)
    assert next_run_at(DailySchedule(enabled=False, times=(NINE,)), now=now) is None
    assert next_run_at(DailySchedule(enabled=True, times=()), now=now) is None


# --------------------------------------------------------------------------- #
# due_run_at
# --------------------------------------------------------------------------- #
def test_due_just_after_the_slot() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, 1)

    assert due_run_at(schedule, last_run_date=None, now=now) == datetime(2026, 9, 21, 9, 0)


def test_not_due_before_the_slot() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 8, 59)

    assert due_run_at(schedule, last_run_date=None, now=now) is None


def test_not_due_twice_on_the_same_day() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, 5)

    assert due_run_at(schedule, last_run_date=date(2026, 9, 21), now=now) is None


def test_due_again_on_the_next_day() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 22, 9, 5)

    assert due_run_at(schedule, last_run_date=date(2026, 9, 21), now=now) == datetime(2026, 9, 22, 9, 0)


def test_catch_up_within_the_grace_window() -> None:
    """**这是这个功能能不能用起来的关键**：服务不是常驻的。

    本地优先意味着用户关机就没有调度器；"9:00 该跑但服务 9:20 才起来"必须能补跑。
    """
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, DEFAULT_GRACE_MINUTES - 1)

    assert due_run_at(schedule, last_run_date=None, now=now) is not None


def test_expired_grace_window_is_not_caught_up() -> None:
    """错过太久就不补了：下午开机不该把早上的投递补上（岗位与 HR 状态都变了）。"""
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, DEFAULT_GRACE_MINUTES + 1)

    assert due_run_at(schedule, last_run_date=None, now=now) is None


def test_grace_window_boundary_is_inclusive() -> None:
    """恰好等于窗口边界算"还来得及"——边界必须明确，不能靠浮点巧合。"""
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, DEFAULT_GRACE_MINUTES)

    assert due_run_at(schedule, last_run_date=None, now=now) is not None


def test_custom_grace_minutes_is_respected() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 11, 0)

    assert due_run_at(schedule, last_run_date=None, now=now, grace_minutes=180) is not None
    assert due_run_at(schedule, last_run_date=None, now=now, grace_minutes=30) is None


def test_zero_grace_does_not_catch_up() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, 1)

    assert due_run_at(schedule, last_run_date=None, now=now, grace_minutes=0) is None


def test_stale_slot_does_not_block_a_still_valid_later_slot() -> None:
    """9:00 与 9:30 两个时间点、现在 9:40：9:00 已过期（40 分钟 > 30），9:30 正该跑。

    **只看最早那个就放弃是错的**——用户明明排了两个时间点，结果一个都没跑。
    """
    schedule = DailySchedule(times=(NINE, NINE_THIRTY), enabled=True)
    now = datetime(2026, 9, 21, 9, 40)

    assert due_run_at(schedule, last_run_date=None, now=now) == datetime(2026, 9, 21, 9, 30)


def test_all_slots_stale_does_not_trigger() -> None:
    """两个都过期了就都不跑（补跑窗口的意义就在这里）。"""
    schedule = DailySchedule(times=(NINE, NINE_THIRTY), enabled=True)
    now = datetime(2026, 9, 21, 11, 0)

    assert due_run_at(schedule, last_run_date=None, now=now) is None


def test_later_slot_alone_still_triggers() -> None:
    schedule = DailySchedule(times=(NINE, NINE_THIRTY), enabled=True)
    now = datetime(2026, 9, 21, 9, 35)

    # 9:00 已经超过补跑窗口（35 分钟），但 9:30 还在窗口内
    assert due_run_at(schedule, last_run_date=None, now=now) == datetime(2026, 9, 21, 9, 30)


def test_due_run_is_none_when_disabled() -> None:
    schedule = DailySchedule(times=(NINE,), enabled=False)
    now = datetime(2026, 9, 21, 9, 5)

    assert due_run_at(schedule, last_run_date=None, now=now) is None


def test_due_run_never_returns_a_future_moment() -> None:
    """不变式：不能返回一个还没到的时刻，否则调度器会一直"该跑了"。"""
    schedule = DailySchedule(times=(time(23, 59),), enabled=True)
    now = datetime(2026, 9, 21, 0, 1)

    assert due_run_at(schedule, last_run_date=None, now=now) is None


def test_last_run_date_in_the_future_does_not_trigger() -> None:
    """时钟回拨/手改配置导致 last_run_date 在未来时，不能反复触发。"""
    schedule = DailySchedule(times=(NINE,), enabled=True)
    now = datetime(2026, 9, 21, 9, 5)

    assert due_run_at(schedule, last_run_date=date(2026, 9, 25), now=now) is None


# --------------------------------------------------------------------------- #
# 描述
# --------------------------------------------------------------------------- #
def test_describe_schedule_is_honest_when_misconfigured() -> None:
    assert "未启用" in describe_schedule(DailySchedule(enabled=False, times=(NINE,)))
    # 开了但没填时间：必须说清"不会自动跑"，不能让用户以为它在工作
    assert "不会自动跑" in describe_schedule(DailySchedule(enabled=True, times=()))
    assert describe_schedule(DailySchedule(enabled=True, times=(NINE, NINE_THIRTY))) == "每天 09:00、09:30"
