"""`apply_settings` 与能力开关的接线测试。

关注点不是能力闸门本身的逻辑（那在 `test_apply_capabilities.py`），而是
**换新存储后老配置的行为有没有变**——这是这次改动最容易伤到现有用户的地方。

所有用例都要 `client`：它负责把 DATA_DIR 指到 tmp_path 并重置引擎，
`session_scope()` 只有在它之后就绪。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.apply.capabilities import (
    CAPABILITIES,
    TIER_CONFIRM_PHRASES,
    Tier,
    gate,
)
from app.apply.runner import ApplySettings, load_settings, save_settings
from app.db import session_scope
from app.models.system import AppSetting


def _write_raw(db: Session, value: dict[str, Any]) -> None:
    """直接往库里写一份"老格式"的 apply_settings（没有 capabilities 字段）。"""
    db.add(AppSetting(key="apply_settings", value=value))
    db.commit()


# --------------------------------------------------------------------------- #
# 向后兼容：老配置读进来 == 引入这一层之前的行为
# --------------------------------------------------------------------------- #
def test_legacy_settings_without_capabilities_load_as_all_off(client: Any) -> None:
    with session_scope() as db:
        _write_raw(
            db,
            {
                "enabled": True,
                "risk_acknowledged": True,
                "platform": "boss",
                "default_channel": "boss",
                "want_salary_min": 20000,
                "selectors": {"search_input": "input.input"},
            },
        )
        settings = load_settings(db)

    assert settings.enabled is True
    assert settings.platform == "boss"
    assert settings.want_salary_min == 20000
    assert settings.selectors == {"search_input": "input.input"}

    # 关键：所有能力都是关的
    for item in CAPABILITIES:
        assert settings.capabilities.wants(item.key) is False, f"{item.key} 不该被默认打开"


def test_legacy_risk_acknowledgement_still_opens_t1(client: Any) -> None:
    """老库只有顶层 risk_acknowledged —— T1 能力仍应可用（不能把用户的确认弄丢）。"""
    with session_scope() as db:
        _write_raw(db, {"risk_acknowledged": True, "capabilities": {"enabled": {"notify_webhook": True}}})
        settings = load_settings(db)

    assert settings.capabilities.risk_acknowledged is True
    assert gate("notify_webhook", settings.capabilities).allowed is True


def test_missing_capabilities_key_does_not_break_load(client: Any) -> None:
    with session_scope() as db:
        _write_raw(db, {})
        settings = load_settings(db)

    assert settings.capabilities.risk_acknowledged is False
    assert settings.platform == "mock"


# --------------------------------------------------------------------------- #
# 往返：存进去再读出来，能力选择不丢
# --------------------------------------------------------------------------- #
def test_capability_choices_survive_round_trip(client: Any) -> None:
    with session_scope() as db:
        settings = ApplySettings(enabled=True, platform="boss")
        settings.capabilities.enabled["scheduled_queue"] = True
        settings.capabilities.signed_phrases = (TIER_CONFIRM_PHRASES[Tier.T2],)
        settings.capabilities.captcha_mode = "wait"
        save_settings(db, settings)

        restored = load_settings(db)

    assert restored.capabilities.wants("scheduled_queue") is True
    assert restored.capabilities.wants("auto_apply") is False
    assert restored.capabilities.signed_phrases == (TIER_CONFIRM_PHRASES[Tier.T2],)
    assert restored.capabilities.captcha_mode == "wait"


def test_save_keeps_both_risk_flags_in_sync(client: Any) -> None:
    """顶层与 capabilities 里那份必须一致，否则 UI 与闸门会各说各话。"""
    with session_scope() as db:
        settings = ApplySettings(risk_acknowledged=True)
        settings.capabilities.risk_acknowledged = False  # 故意不一致
        save_settings(db, settings)

        row = db.exec(select(AppSetting).where(AppSetting.key == "apply_settings")).first()
        assert row is not None
        assert row.value["risk_acknowledged"] is True
        assert row.value["capabilities"]["risk_acknowledged"] is True

        restored = load_settings(db)

    assert restored.risk_acknowledged is True
    assert restored.capabilities.risk_acknowledged is True


def test_saving_defaults_does_not_turn_anything_on(client: Any) -> None:
    """把一个默认 ApplySettings 存下去，读回来仍应全部关闭。"""
    with session_scope() as db:
        save_settings(db, ApplySettings())
        restored = load_settings(db)

    for item in CAPABILITIES:
        assert restored.capabilities.is_on(item.key) is False


def test_unknown_keys_in_stored_capabilities_are_ignored(client: Any) -> None:
    """手改配置文件塞进垃圾键不能让加载炸掉，也不能让未知能力变成"可用"。"""
    with session_scope() as db:
        _write_raw(
            db,
            {"capabilities": {"enabled": {"totally_made_up": True, "auto_apply": True}}},
        )
        settings = load_settings(db)

    assert settings.capabilities.wants("auto_apply") is True
    # 未知键不落进 enabled，所以 T2 闸门仍然拦着（没签短语）
    assert gate("auto_apply", settings.capabilities).allowed is False
