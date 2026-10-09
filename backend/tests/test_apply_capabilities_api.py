"""能力开关的 API 测试（GET /api/apply/capabilities 与 PUT /api/apply/settings）。

这一层要证明的是**接线是对的**：闸门逻辑本身在 `test_apply_capabilities.py`。
关注三件事：
1. 默认全关，且面板把每个能力的"为什么没生效"讲清楚；
2. 写进去的选择能读回来，已签的确认短语不会被普通保存清掉；
3. 云端（run_local=false）拿不到 T3 档能力。
"""

from __future__ import annotations

from typing import Any

from app.apply.capabilities import CAPABILITIES, TIER_CONFIRM_PHRASES, Tier

SETTINGS_URL = "/api/apply/settings"
CAPS_URL = "/api/apply/capabilities"


def _by_key(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in payload["items"]}


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def test_capabilities_endpoint_lists_every_capability(client: Any) -> None:
    response = client.get(CAPS_URL)
    assert response.status_code == 200

    payload = response.json()
    items = _by_key(payload)
    assert set(items) == {item.key for item in CAPABILITIES}

    # 每个都有可读的中文与档位说明——前端不该硬编码这些
    for item in items.values():
        assert item["label"]
        assert item["summary"]
        assert item["tier_label"]
        assert item["tier_summary"]


def test_everything_is_off_by_default(client: Any) -> None:
    payload = client.get(CAPS_URL).json()

    for item in payload["items"]:
        assert item["wanted"] is False, f"{item['key']} 不该被默认勾选"
        assert item["on"] is False, f"{item['key']} 不该被默认生效"
        assert item["blocked_reason"]  # 没生效就该有原因可显示

    assert payload["risk_acknowledged"] is False
    assert payload["any_auto_enabled"] is False
    assert payload["captcha_mode"] == "circuit"


def test_captcha_modes_expose_no_bypass_option(client: Any) -> None:
    """界面上的验证码策略只能有"停下"和"等人"，**不能出现自动绕过**。"""
    payload = client.get(CAPS_URL).json()
    keys = {mode["key"] for mode in payload["captcha_modes"]}

    assert keys == {"circuit", "wait"}
    for mode in payload["captcha_modes"]:
        assert mode["label"] and mode["detail"]
        assert "绕过" not in mode["label"]
        assert "破解" not in mode["detail"]


# --------------------------------------------------------------------------- #
# 写：T0 / T1
# --------------------------------------------------------------------------- #
def _base_settings(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"enabled": True, "platform": "mock"}
    body.update(overrides)
    return body


def test_t0_capability_can_be_turned_on_without_risk_acknowledgement(client: Any) -> None:
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {"resume_attachment": True}}),
    )
    assert response.status_code == 200

    item = _by_key(response.json()["capabilities"])["resume_attachment"]
    assert item["wanted"] is True
    assert item["on"] is True
    assert item["blocked_reason"] == ""


def test_t1_capability_needs_risk_acknowledgement(client: Any) -> None:
    # 没勾风险确认 → 只是被勾选，不生效
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {"notify_webhook": True}}),
    )
    item = _by_key(response.json()["capabilities"])["notify_webhook"]
    assert item["wanted"] is True
    assert item["on"] is False
    assert "风险" in item["blocked_reason"]

    # 勾上风险确认后生效
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={"enabled": {"notify_webhook": True}},
        ),
    )
    item = _by_key(response.json()["capabilities"])["notify_webhook"]
    assert item["on"] is True


# --------------------------------------------------------------------------- #
# 写：T2 / T3 的短语门槛
# --------------------------------------------------------------------------- #
def test_t2_reports_which_phrase_is_missing(client: Any) -> None:
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={"enabled": {"scheduled_queue": True, "auto_apply": True}},
        ),
    )
    item = _by_key(response.json()["capabilities"])["auto_apply"]
    assert item["on"] is False
    assert item["missing_phrase"] == TIER_CONFIRM_PHRASES[Tier.T2]
    assert "确认短语" in item["blocked_reason"]


def test_t2_opens_after_signing_the_phrase(client: Any) -> None:
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {"scheduled_queue": True, "auto_apply": True},
                "signed_phrases": [TIER_CONFIRM_PHRASES[Tier.T2]],
            },
        ),
    )
    payload = response.json()["capabilities"]
    item = _by_key(payload)["auto_apply"]
    assert item["on"] is True
    assert payload["any_auto_enabled"] is True


def test_signed_phrase_survives_a_later_plain_save(client: Any) -> None:
    """**关键回归**：签过 T2 短语后，再保存一次不含短语的设置，不能把确认弄丢。

    否则用户会莫名其妙地发现"自动投递自己关了"。
    """
    client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {"scheduled_queue": True, "auto_apply": True},
                "signed_phrases": [TIER_CONFIRM_PHRASES[Tier.T2]],
            },
        ),
    )

    # 一次普通的设置保存（比如只改限速），请求体里没有 signed_phrases
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            per_day=25,
            capabilities={"enabled": {"scheduled_queue": True, "auto_apply": True}},
        ),
    )
    assert response.status_code == 200
    payload = response.json()

    assert TIER_CONFIRM_PHRASES[Tier.T2] in payload["capabilities"]["signed_phrases"]
    assert _by_key(payload["capabilities"])["auto_apply"]["on"] is True


def test_t3_is_blocked_when_not_running_locally(client: Any) -> None:
    """云端：T3 档能力一律不生效，且原因要说明是本地运行的限制。"""
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {"multi_account": True},
                "signed_phrases": [
                    TIER_CONFIRM_PHRASES[Tier.T2],
                    TIER_CONFIRM_PHRASES[Tier.T3],
                ],
                "run_local": False,
            },
        ),
    )
    item = _by_key(response.json()["capabilities"])["multi_account"]
    assert item["wanted"] is True
    assert item["on"] is False
    assert "本地" in item["blocked_reason"]


def test_t3_opens_locally_with_both_phrases(client: Any) -> None:
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {"multi_account": True},
                "signed_phrases": [
                    TIER_CONFIRM_PHRASES[Tier.T2],
                    TIER_CONFIRM_PHRASES[Tier.T3],
                ],
                "run_local": True,
            },
        ),
    )
    item = _by_key(response.json()["capabilities"])["multi_account"]
    assert item["on"] is True


def test_t3_without_t2_phrase_reports_the_t2_phrase(client: Any) -> None:
    """不许跳级：只签了 T3 的短语时，欠的是 T2 那句。"""
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {"proxy_pool": True},
                "signed_phrases": [TIER_CONFIRM_PHRASES[Tier.T3]],
            },
        ),
    )
    item = _by_key(response.json()["capabilities"])["proxy_pool"]
    assert item["on"] is False
    assert item["missing_phrase"] == TIER_CONFIRM_PHRASES[Tier.T2]


# --------------------------------------------------------------------------- #
# 写：一致性
# --------------------------------------------------------------------------- #
def test_omitting_a_capability_turns_it_off(client: Any) -> None:
    """前端整份回传 `enabled`，所以没提到 = 取消勾选。"""
    client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {"resume_attachment": True}}),
    )
    response = client.put(SETTINGS_URL, json=_base_settings(capabilities={"enabled": {}}))

    item = _by_key(response.json()["capabilities"])["resume_attachment"]
    assert item["wanted"] is False
    assert item["on"] is False


def test_disabling_auto_apply_also_disables_scheduled_apply(client: Any) -> None:
    client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {
                    "scheduled_queue": True,
                    "auto_apply": True,
                    "scheduled_apply": True,
                },
                "signed_phrases": [TIER_CONFIRM_PHRASES[Tier.T2]],
            },
        ),
    )

    response = client.put(
        SETTINGS_URL,
        json=_base_settings(
            risk_acknowledged=True,
            capabilities={
                "enabled": {"scheduled_queue": True, "auto_apply": False, "scheduled_apply": True},
            },
        ),
    )
    items = _by_key(response.json()["capabilities"])
    assert items["auto_apply"]["wanted"] is False
    assert items["scheduled_apply"]["wanted"] is False


def test_settings_without_capabilities_field_is_harmless(client: Any) -> None:
    """老前端不知道这个字段，PUT 不带它时不能报错，也不能把已有选择清空。"""
    client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {"resume_attachment": True}}),
    )

    response = client.put(SETTINGS_URL, json=_base_settings())
    assert response.status_code == 200

    payload = response.json()
    # 不带 capabilities 字段 → 保留原选择，而不是清空
    assert _by_key(payload["capabilities"])["resume_attachment"]["wanted"] is True


def test_settings_response_carries_capabilities(client: Any) -> None:
    """GET /settings 也要带上能力状态（前端只取一个接口就够渲染整页）。"""
    payload = client.get(SETTINGS_URL).json()
    assert "capabilities" in payload
    assert len(payload["capabilities"]["items"]) == len(CAPABILITIES)


def test_unknown_capability_key_in_request_is_ignored(client: Any) -> None:
    """请求体里塞未知能力不能 500，也不能让它变成可用。"""
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {"totally_made_up": True}}),
    )
    assert response.status_code == 200

    items = _by_key(response.json()["capabilities"])
    assert "totally_made_up" not in items
    assert all(item["on"] is False for item in items.values())


def test_captcha_mode_choice_round_trips(client: Any) -> None:
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {}, "captcha_mode": "wait"}),
    )
    assert response.json()["capabilities"]["captcha_mode"] == "wait"

    assert client.get(CAPS_URL).json()["captcha_mode"] == "wait"


def test_unknown_captcha_mode_falls_back_to_circuit(client: Any) -> None:
    response = client.put(
        SETTINGS_URL,
        json=_base_settings(capabilities={"enabled": {}, "captcha_mode": "bypass"}),
    )
    assert response.json()["capabilities"]["captcha_mode"] == "circuit"
