"""定时投递：配置存取、闸门与预览。

纯时间计算在 `test_apply_schedule.py`；这里管的是"存得对、闸门拦得住、界面看得出状态"。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlmodel import select

from app.apply.capabilities import CAPABILITIES, TIER_CONFIRM_PHRASES, Tier
from app.apply.schedule_store import (
    MODE_APPLY,
    MODE_QUEUE,
    ScheduleSettings,
    load_schedule,
    mark_run,
    save_schedule,
)
from app.db import session_scope
from app.models.system import AppSetting

SCHEDULE_URL = "/api/apply/schedule"
SETTINGS_URL = "/api/apply/settings"


def _enable_capabilities(client: Any, keys: list[str], phrases: list[str] | None = None) -> None:
    enabled = {item.key: False for item in CAPABILITIES}
    for key in keys:
        enabled[key] = True
    response = client.put(
        SETTINGS_URL,
        json={
            "enabled": True,
            "risk_acknowledged": True,
            "capabilities": {"enabled": enabled, "signed_phrases": phrases or []},
        },
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# 存取
# --------------------------------------------------------------------------- #
def test_schedule_defaults_to_off(client: Any) -> None:
    with session_scope() as db:
        settings = load_schedule(db)

    assert settings.enabled is False
    assert settings.mode == MODE_QUEUE
    assert settings.last_run_date is None


def test_schedule_round_trip(client: Any) -> None:
    with session_scope() as db:
        save_schedule(
            db,
            ScheduleSettings(
                enabled=True,
                times=["18:00", "09:30"],
                mode=MODE_QUEUE,
                limit=7,
                keywords=["视觉", "标定"],
                city="101010100",
                fetch_detail=True,
            ),
        )
        restored = load_schedule(db)

    assert restored.enabled is True
    # 存的时候已经排序解析过，读回来就是规范形式
    assert restored.times == ["09:30", "18:00"]
    assert restored.limit == 7
    assert restored.keywords == ["视觉", "标定"]
    assert restored.fetch_detail is True


def test_unparseable_times_are_dropped_at_write_time(client: Any) -> None:
    """认不出的时间点别让它进库：否则以后每次读都要重新过滤一遍。"""
    with session_scope() as db:
        save_schedule(db, ScheduleSettings(enabled=True, times=["09:00", "garbage", "25:00"]))
        restored = load_schedule(db)

    assert restored.times == ["09:00"]


def test_unknown_mode_falls_back_to_queue(client: Any) -> None:
    """模式认不出时退回"只入队"——**绝不能默认成自动投递**。"""
    with session_scope() as db:
        save_schedule(db, ScheduleSettings(mode="anything"))
        restored = load_schedule(db)

    assert restored.mode == MODE_QUEUE


def test_mark_run_records_date_and_result(client: Any) -> None:
    with session_scope() as db:
        save_schedule(db, ScheduleSettings(enabled=True, times=["09:00"]))
        mark_run(db, at=datetime(2026, 9, 21, 9, 0), result="入队 6 条")
        restored = load_schedule(db)

    assert restored.last_run_date == date(2026, 9, 21)
    assert restored.last_run_at == datetime(2026, 9, 21, 9, 0)
    assert restored.last_result == "入队 6 条"


def test_saving_schedule_keeps_run_history(client: Any) -> None:
    """改排期不该把"上次跑过"的记录清掉——否则会立刻重复跑一次。"""
    with session_scope() as db:
        save_schedule(db, ScheduleSettings(enabled=True, times=["09:00"]))
        mark_run(db, at=datetime(2026, 9, 21, 9, 0), result="入队 3 条")
        save_schedule(db, load_schedule(db))
        restored = load_schedule(db)

    assert restored.last_run_date == date(2026, 9, 21)


def test_corrupt_stored_values_do_not_break_loading(client: Any) -> None:
    """手改配置文件塞进垃圾值时，加载要退化成默认值而不是 500。"""
    with session_scope() as db:
        db.add(
            AppSetting(
                key="apply_schedule",
                value={"times": "not-a-list", "last_run_date": "garbage", "limit": "abc"},
            )
        )
        db.commit()
        settings = load_schedule(db)

    assert settings.times == []
    assert settings.last_run_date is None
    assert settings.limit >= 1


# --------------------------------------------------------------------------- #
# API：只入队可以随便开；自动投递要过 T2 闸门
# --------------------------------------------------------------------------- #
def test_schedule_endpoint_defaults(client: Any) -> None:
    payload = client.get(SCHEDULE_URL).json()

    assert payload["enabled"] is False
    assert payload["mode"] == MODE_QUEUE
    assert payload["active"] is False
    assert payload["max_slots"] == 6
    assert set(payload["mode_labels"]) == {MODE_QUEUE, MODE_APPLY}


def test_queue_mode_can_be_saved_without_any_capability(client: Any) -> None:
    """只抓队列不投递，属于 T0，不需要任何风险确认。"""
    response = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": ["09:00"], "mode": MODE_QUEUE, "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["parsed_times"] == ["09:00"]
    assert payload["active"] is True
    assert "09:00" in payload["description"]


def test_apply_mode_is_refused_without_the_capability(client: Any) -> None:
    """**关键**：没开自动投递就不能把模式设成自动投递。

    不能"存下来再在跑的时候静默降级"——用户会以为设好了，实际什么都没发生。
    """
    response = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": ["09:00"], "mode": MODE_APPLY},
    )

    assert response.status_code == 403
    assert "定时自动投递" in response.json()["detail"]

    # 而且不该被存进去
    assert client.get(SCHEDULE_URL).json()["mode"] == MODE_QUEUE


def test_apply_mode_is_allowed_once_capabilities_are_open(client: Any) -> None:
    _enable_capabilities(
        client,
        ["scheduled_queue", "auto_apply", "scheduled_apply"],
        phrases=[TIER_CONFIRM_PHRASES[Tier.T2]],
    )

    response = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": ["09:00"], "mode": MODE_APPLY},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == MODE_APPLY


def test_apply_mode_requires_the_phrase_not_just_the_toggle(client: Any) -> None:
    """勾了但没签确认短语时，闸门不开 → 依然拒绝。"""
    _enable_capabilities(client, ["scheduled_queue", "auto_apply", "scheduled_apply"], phrases=[])

    response = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": ["09:00"], "mode": MODE_APPLY},
    )

    assert response.status_code == 403


def test_unknown_mode_in_request_falls_back_to_queue(client: Any) -> Any:
    response = client.put(SCHEDULE_URL, json={"enabled": True, "times": ["09:00"], "mode": "nonsense"})

    assert response.status_code == 200
    assert response.json()["mode"] == MODE_QUEUE


def test_invalid_time_is_reported_as_unparsed(client: Any) -> None:
    """填错时间不能报错，但界面必须能看出它没生效。"""
    payload = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": ["09:00", "99:99"], "mode": MODE_QUEUE},
    ).json()

    assert payload["parsed_times"] == ["09:00"]


def test_enabled_without_times_is_not_active(client: Any) -> None:
    """开了但没填时间：`active` 必须是 False，description 也要说清不会自动跑。"""
    payload = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": [], "mode": MODE_QUEUE},
    ).json()

    assert payload["active"] is False
    assert "不会自动跑" in payload["description"]
    assert payload["next_run_at"] is None


def test_next_run_at_is_exposed_for_the_ui(client: Any) -> None:
    payload = client.put(
        SCHEDULE_URL,
        json={"enabled": True, "times": ["23:59"], "mode": MODE_QUEUE},
    ).json()

    assert payload["next_run_at"] is not None


def test_schedule_survives_a_reload(client: Any) -> None:
    client.put(SCHEDULE_URL, json={"enabled": True, "times": ["10:00"], "limit": 4, "mode": MODE_QUEUE})
    payload = client.get(SCHEDULE_URL).json()

    assert payload["enabled"] is True
    assert payload["limit"] == 4


def test_schedule_does_not_leak_into_apply_settings(client: Any) -> None:
    """排期存在自己的 key 里，不该污染 apply_settings。"""
    # 先各写一次，确认两个 key 各自独立存在
    client.put(SCHEDULE_URL, json={"enabled": True, "times": ["10:00"], "mode": MODE_QUEUE})
    client.put(SETTINGS_URL, json={"enabled": True, "per_day": 12})

    with session_scope() as db:
        keys = {row.key for row in db.exec(select(AppSetting)).all()}
        schedule_row = db.exec(select(AppSetting).where(AppSetting.key == "apply_schedule")).first()
        apply_row = db.exec(select(AppSetting).where(AppSetting.key == "apply_settings")).first()

    assert "apply_schedule" in keys
    assert "apply_settings" in keys
    # 排期的字段不该出现在 apply_settings 里，反之亦然
    assert "times" in schedule_row.value  # type: ignore[union-attr]
    assert "times" not in apply_row.value  # type: ignore[union-attr]
    assert "per_day" not in schedule_row.value  # type: ignore[union-attr]
