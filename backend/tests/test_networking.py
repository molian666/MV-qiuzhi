"""人脉跟进：到期计算、逾期分级、跟进话术、生日与节日。

验收标准是「到期联系人不再漏跟进」——所以核心测试是**到期时间算得对**：
内推人、HR、猎头、校友的节奏不同，承诺的日期还要能覆盖默认节奏。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis.networking import (
    DEFAULT_FOLLOW_UP_DAYS,
    ContactSnapshot,
    InteractionSnapshot,
    build_follow_ups,
    greeting_for,
    next_follow_up,
    outstanding_promise,
    relation_label,
    relation_options,
    relationship_health,
    resolve_cadence,
    suggest_message,
    timeline,
)

NOW = datetime(2026, 5, 20, 10, 0, 0, tzinfo=timezone.utc)


def _contact(**overrides: object) -> ContactSnapshot:
    base: dict[str, object] = {
        "id": 1,
        "name": "老王",
        "relation": "referral",
        "company": "某某科技",
    }
    base.update(overrides)
    return ContactSnapshot(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 节奏与到期
# --------------------------------------------------------------------------- #
def test_cadence_follows_relation() -> None:
    assert resolve_cadence(_contact(relation="referral")) == DEFAULT_FOLLOW_UP_DAYS["referral"]
    assert resolve_cadence(_contact(relation="alumni")) == DEFAULT_FOLLOW_UP_DAYS["alumni"]
    # 内推比校友要紧——内推完不及时问进展，事情就凉了
    assert DEFAULT_FOLLOW_UP_DAYS["referral"] < DEFAULT_FOLLOW_UP_DAYS["alumni"]


def test_explicit_cadence_overrides_the_default() -> None:
    assert resolve_cadence(_contact(follow_up_days=2)) == 2


def test_follow_up_follows_the_cadence() -> None:
    contact = _contact(last_contact_at=NOW - timedelta(days=3))
    assert next_follow_up(contact, now=NOW) == NOW - timedelta(days=3) + timedelta(days=5)


def test_explicit_next_follow_up_wins() -> None:
    contact = _contact(
        interactions=[
            InteractionSnapshot(at=NOW - timedelta(days=1), next_follow_up_at=NOW + timedelta(days=2))
        ]
    )
    assert next_follow_up(contact, now=NOW) == NOW + timedelta(days=2)


def test_overdue_appointment_is_reported_as_due() -> None:
    contact = _contact(
        interactions=[
            InteractionSnapshot(at=NOW - timedelta(days=10), next_follow_up_at=NOW - timedelta(days=4))
        ]
    )
    due = next_follow_up(contact, now=NOW)
    assert due == NOW - timedelta(days=4)


def test_never_contacted_uses_created_at() -> None:
    contact = _contact(created_at=NOW - timedelta(days=10), last_contact_at=None)
    assert next_follow_up(contact, now=NOW) == NOW - timedelta(days=10) + timedelta(days=5)


def test_no_dates_at_all() -> None:
    assert next_follow_up(_contact(created_at=None, last_contact_at=None), now=NOW) is None


# --------------------------------------------------------------------------- #
# 跟进列表
# --------------------------------------------------------------------------- #
def test_follow_ups_flag_overdue_first() -> None:
    fresh = _contact(id=1, name="刚聊过", last_contact_at=NOW - timedelta(days=1))
    stale = _contact(id=2, name="好久没聊", last_contact_at=NOW - timedelta(days=30))

    rows = build_follow_ups([fresh, stale], now=NOW)
    assert rows
    assert rows[0].contact_id == 2
    assert rows[0].urgency == "overdue"
    assert rows[0].days_overdue > 0
    assert "30 天前" in rows[0].reason or "超过" in rows[0].reason


def test_follow_ups_respect_horizon() -> None:
    far = _contact(id=1, name="很久以后", relation="alumni", last_contact_at=NOW)
    rows = build_follow_ups([far], now=NOW, horizon_days=3)
    assert rows == []
    assert build_follow_ups([far], now=NOW, horizon_days=90)


def test_urgency_levels() -> None:
    overdue = _contact(id=1, name="逾期", last_contact_at=NOW - timedelta(days=20))
    today = _contact(id=2, name="今天", relation="referral", last_contact_at=NOW - timedelta(days=5))
    soon = _contact(id=3, name="快到了", relation="referral", last_contact_at=NOW - timedelta(days=3))

    rows = {item.name: item for item in build_follow_ups([overdue, today, soon], now=NOW)}
    assert rows["逾期"].urgency == "overdue"
    assert rows["今天"].urgency == "today"
    assert rows["快到了"].urgency in {"soon", "later"}


def test_referral_wins_on_same_overdue_days() -> None:
    referral = _contact(id=1, name="内推人", relation="referral", last_contact_at=NOW - timedelta(days=12))
    alumni = _contact(id=2, name="校友", relation="alumni", follow_up_days=5, last_contact_at=NOW - timedelta(days=12))

    rows = build_follow_ups([alumni, referral], now=NOW)
    assert rows[0].days_overdue == rows[1].days_overdue
    assert rows[0].relation == "referral"


def test_promise_is_surfaced() -> None:
    contact = _contact(
        last_contact_at=NOW - timedelta(days=10),
        interactions=[InteractionSnapshot(at=NOW - timedelta(days=10), promised="帮你问一下 HC")],
    )
    row = build_follow_ups([contact], now=NOW)[0]
    assert row.promise == "帮你问一下 HC"
    assert "HC" in row.suggested
    assert "HC" in row.reason


# --------------------------------------------------------------------------- #
# 话术与问候
# --------------------------------------------------------------------------- #
def test_message_varies_by_relation() -> None:
    referral = suggest_message(_contact(relation="referral"), now=NOW)
    hr = suggest_message(_contact(relation="hr"), now=NOW)
    headhunter = suggest_message(_contact(relation="headhunter"), now=NOW)

    assert "内推" in referral
    assert "流程" in hr or "反馈" in hr
    assert "机会" in headhunter or "JD" in headhunter
    # 不同关系的话术必须真的不同，否则就等于模板群发
    assert len({referral, hr, headhunter}) == 3


def test_message_is_not_empty_filler() -> None:
    text = suggest_message(_contact(), now=NOW)
    # 不能是「在吗」这种没有信息量的开场
    assert len(text) > 20
    assert "在吗" not in text


def test_promise_message_beats_relation_template() -> None:
    text = suggest_message(_contact(relation="headhunter"), now=NOW, promise="帮我看看有没有 HC")
    assert "帮我看看有没有 HC" in text


def test_outstanding_promise_uses_the_latest() -> None:
    contact = _contact(
        interactions=[
            InteractionSnapshot(at=NOW - timedelta(days=30), promised="旧承诺"),
            InteractionSnapshot(at=NOW - timedelta(days=1), promised="新承诺"),
        ]
    )
    assert outstanding_promise(contact) == "新承诺"


def test_birthday_greeting() -> None:
    contact = _contact(key_dates=[{"label": "生日", "month": 5, "day": 20}])
    greeting = greeting_for(contact, now=NOW)
    assert greeting and "生日" in greeting
    assert greeting_for(contact, now=NOW + timedelta(days=1)) is None


def test_fixed_holiday_greeting() -> None:
    greeting = greeting_for(_contact(), now=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
    assert greeting and "国庆" in greeting


def test_broken_key_date_does_not_crash() -> None:
    contact = _contact(key_dates=[{"label": "生日", "month": "五月", "day": None}])
    assert greeting_for(contact, now=NOW) is None


# --------------------------------------------------------------------------- #
# 健康度与时间线
# --------------------------------------------------------------------------- #
def test_relationship_health_levels() -> None:
    warm = relationship_health(_contact(last_contact_at=NOW - timedelta(days=1)), now=NOW)
    due = relationship_health(_contact(last_contact_at=NOW - timedelta(days=6)), now=NOW)
    stale = relationship_health(_contact(last_contact_at=NOW - timedelta(days=20)), now=NOW)
    cold = relationship_health(_contact(last_contact_at=None, created_at=None), now=NOW)

    assert warm["level"] == "warm"
    assert due["level"] == "due"
    assert stale["level"] == "stale"
    assert cold["level"] == "cold"


def test_timeline_is_newest_first() -> None:
    rows = timeline(
        [
            InteractionSnapshot(at=NOW - timedelta(days=5), summary="第一次聊"),
            InteractionSnapshot(at=NOW - timedelta(days=1), summary="最近一次"),
        ]
    )
    assert [item["summary"] for item in rows] == ["最近一次", "第一次聊"]
    assert rows[0]["direction_label"] == "我联系他"


def test_relation_options_cover_all_relations() -> None:
    options = relation_options()
    assert {item["key"] for item in options} >= {"referral", "alumni", "hr", "headhunter"}
    assert all(item["label"] and item["default_follow_up_days"] > 0 for item in options)
    assert relation_label("referral") == "内推人"
    assert relation_label("unknown") == "unknown"


def test_empty_contacts() -> None:
    assert build_follow_ups([], now=NOW) == []
