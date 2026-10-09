"""能力开关与分级闸门的测试。

这一层的价值全在"**默认关、不许跳级**"两条上，所以测试也主要盯这两条：
一旦有人把某个能力的 default 改成 True，或把闸门写成"只要勾了就放行"，
这里的用例必须立刻变红。
"""

from __future__ import annotations

import pytest

from app.apply.capabilities import (
    CAPABILITIES,
    CAPTCHA_MODE_CIRCUIT,
    CAPTCHA_MODE_WAIT,
    TIER_CONFIRM_PHRASES,
    CapabilityDenied,
    CapabilityState,
    Tier,
    capability,
    default_state,
    enable,
    gate,
    refuse,
    state_from_dict,
    state_to_dict,
)

# --------------------------------------------------------------------------- #
# 默认档位 = 老行为
# --------------------------------------------------------------------------- #
def test_default_capabilities_are_all_off() -> None:
    """所有能力默认关闭——升级后老用户的行为必须一个字都不变。"""
    for item in CAPABILITIES:
        assert item.default is False, f"{item.key} 的默认值必须是 False"

    state = default_state()
    for item in CAPABILITIES:
        assert state.wants(item.key) is False
        assert state.is_on(item.key) is False


def test_default_captcha_mode_is_circuit_not_wait() -> None:
    """验证码的默认策略必须是熔断停投，而不是"等人处理"——后者会一直占着浏览器。"""
    assert default_state().captcha_mode == CAPTCHA_MODE_CIRCUIT


def test_unknown_captcha_mode_falls_back_to_circuit() -> None:
    """认不出的策略退回最保守的那个，而不是放行。"""
    state = state_from_dict({"captcha_mode": "bypass"})
    assert state.captcha_mode == CAPTCHA_MODE_CIRCUIT


def test_unknown_capability_key_is_rejected() -> None:
    with pytest.raises(KeyError):
        capability("no_such_capability")


# --------------------------------------------------------------------------- #
# 逐级闸门
# --------------------------------------------------------------------------- #
def test_t0_needs_nothing_but_its_own_switch() -> None:
    state = CapabilityState(enabled={"resume_attachment": True})
    assert gate("resume_attachment", state).allowed is True


def test_t1_requires_risk_acknowledgement() -> None:
    state = CapabilityState(enabled={"notify_webhook": True})
    denied = gate("notify_webhook", state)
    assert denied.allowed is False
    assert "风险" in denied.reason

    state.risk_acknowledged = True
    assert gate("notify_webhook", state).allowed is True


def test_t2_requires_phrase_on_top_of_acknowledgement() -> None:
    state = CapabilityState(enabled={"scheduled_queue": True, "auto_apply": True}, risk_acknowledged=True)

    denied = gate("auto_apply", state)
    assert denied.allowed is False
    assert denied.missing_phrase == TIER_CONFIRM_PHRASES[Tier.T2]
    assert "确认短语" in denied.reason

    state.signed_phrases = (TIER_CONFIRM_PHRASES[Tier.T2],)
    assert gate("auto_apply", state).allowed is True


def test_t2_phrase_alone_is_not_enough_without_acknowledgement() -> None:
    """签了短语但没勾风险确认，仍然不放行——两个条件都要。"""
    state = CapabilityState(
        enabled={"auto_apply": True},
        risk_acknowledged=False,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
    )
    assert gate("auto_apply", state).allowed is False


def test_t3_requires_t2_phrase_as_well() -> None:
    """**不许跳级**：只想开多账号、没签过 T2 的短语 → 必须被拒。

    这一条是这层设计最重要的约束。少了它，用户可能在没意识到
    "自动投递已经打开"的情况下顺手勾上多账号轮换。
    """
    state = CapabilityState(
        enabled={"multi_account": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T3],),
    )
    denied = gate("multi_account", state)
    assert denied.allowed is False
    assert denied.missing_phrase == TIER_CONFIRM_PHRASES[Tier.T2]

    # 补签 T2 的短语后放行
    state.signed_phrases = (TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3])
    assert gate("multi_account", state).allowed is True


def test_t3_is_refused_when_not_running_locally() -> None:
    """云端一律拒绝这一档：平台按 IP 风控，把封号风险引给用户没有意义。"""
    state = CapabilityState(
        enabled={"proxy_pool": True},
        risk_acknowledged=True,
        run_local=False,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]),
    )
    denied = gate("proxy_pool", state)
    assert denied.allowed is False
    assert "本地" in denied.reason


def test_dependencies_must_be_on_first() -> None:
    """定时自动投递依赖自动投递：不能出现"定时开着、自动没开"的矛盾状态。"""
    state = CapabilityState(
        enabled={"scheduled_apply": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
    )
    denied = gate("scheduled_apply", state)
    assert denied.allowed is False
    assert "自动投递" in denied.reason


def test_dependency_that_is_itself_gated_does_not_count() -> None:
    """依赖项被勾选但本身没过闸门时，不算满足依赖。"""
    state = CapabilityState(
        enabled={"scheduled_apply": True, "auto_apply": True, "scheduled_queue": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
    )
    # 此时 auto_apply 与 scheduled_queue 都满足闸门 → 应当放行
    assert gate("scheduled_apply", state).allowed is True

    # 去掉风险确认后，auto_apply 自己也不过闸门 → 依赖不成立
    state.risk_acknowledged = False
    assert gate("scheduled_apply", state).allowed is False


def test_disabled_capability_reports_plain_reason() -> None:
    denied = gate("auto_apply", default_state())
    assert denied.allowed is False
    assert "未开启" in denied.reason


# --------------------------------------------------------------------------- #
# refuse()：业务代码的统一出口
# --------------------------------------------------------------------------- #
def test_refuse_raises_with_actionable_reason() -> None:
    with pytest.raises(CapabilityDenied) as excinfo:
        refuse("auto_apply", default_state())
    assert "未开启" in str(excinfo.value)


def test_refuse_passes_when_gate_is_open() -> None:
    state = CapabilityState(
        enabled={"scheduled_queue": True, "auto_apply": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
    )
    refuse("auto_apply", state)  # 不抛就是通过


def test_refuse_reports_missing_dependency_not_just_disabled() -> None:
    """只勾了 auto_apply、没勾它的依赖时，报的必须是"缺哪个依赖"，而不是笼统的未开启。"""
    state = CapabilityState(
        enabled={"auto_apply": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
    )
    with pytest.raises(CapabilityDenied) as excinfo:
        refuse("auto_apply", state)
    assert "定时生成待投队列" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# enable() 的一致性
# --------------------------------------------------------------------------- #
def test_enabling_does_not_auto_sign_phrases() -> None:
    """勾选只记意图，短语必须由 API 层从用户请求里拿——不能替用户签。"""
    state = default_state()
    enable(state, "auto_apply")
    assert state.wants("auto_apply") is True
    assert state.signed_phrases == ()
    assert gate("auto_apply", state).allowed is False


def test_disabling_a_capability_disables_its_dependents() -> None:
    """关掉自动投递时，依赖它的定时自动投递必须一起关，不留矛盾状态。"""
    state = CapabilityState(
        enabled={"scheduled_queue": True, "auto_apply": True, "scheduled_apply": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
    )
    assert gate("scheduled_apply", state).allowed is True

    enable(state, "auto_apply", on=False)

    assert state.wants("auto_apply") is False
    assert state.wants("scheduled_apply") is False


def test_enable_rejects_unknown_key() -> None:
    with pytest.raises(KeyError):
        enable(default_state(), "made_up")


# --------------------------------------------------------------------------- #
# 持久化往返
# --------------------------------------------------------------------------- #
def test_round_trip_preserves_everything() -> None:
    state = CapabilityState(
        enabled={"auto_apply": True, "notify_webhook": True},
        risk_acknowledged=True,
        signed_phrases=(TIER_CONFIRM_PHRASES[Tier.T2],),
        run_local=False,
        captcha_mode=CAPTCHA_MODE_WAIT,
    )
    restored = state_from_dict(state_to_dict(state))

    assert restored.wants("auto_apply") is True
    assert restored.wants("notify_webhook") is True
    assert restored.wants("multi_account") is False
    assert restored.risk_acknowledged is True
    assert restored.signed_phrases == state.signed_phrases
    assert restored.run_local is False
    assert restored.captcha_mode == CAPTCHA_MODE_WAIT


def test_state_from_empty_or_garbage_is_all_off() -> None:
    """老库里的 apply_settings 没有 capabilities 字段 —— 读出来必须等于全关。"""
    for payload in (None, {}, {"enabled": "yes"}, {"enabled": {"bogus": True}}):
        state = state_from_dict(payload)
        assert all(state.wants(item.key) is False for item in CAPABILITIES)
        assert state.risk_acknowledged is False


def test_state_to_dict_only_writes_registry_keys() -> None:
    payload = state_to_dict(default_state())
    assert set(payload["enabled"]) == {item.key for item in CAPABILITIES}
