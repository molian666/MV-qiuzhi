"""投递能力开关与分级风险闸门。

**为什么要有这一层**：get_jobs 那类工具把"全自动投递 / 多账号轮换 / 代理池 / 指纹伪装"
当作默认能力直接给用户。它们确实有用，但每一条都可能让账号被封——而封号对正在求职的人来说
代价极高。所以这里的选择是：**能力全部实现，一个都不省，但默认全部关闭，并按风险分级设闸门**。

设计要点：

1. **默认档位 = 老行为**。`Capability.default` 一律 False，所以升级后老库读进来行为完全不变，
   不开启就等于这些能力不存在（`test_default_capabilities_are_all_off` 钉住这条）。
2. **不许跳级**。T3 的能力要求 T2 的确认短语也签过,否则用户可能在没意识到
   "自动投递已经打开"的情况下顺手勾上"多账号轮换"。`gate()` 逐级校验。
3. **不自动破解验证码**。这一条不在开关范围内——见 `CAPTCHA_MODE_*`，只提供
   "熔断停投"(默认) 与 "等待人工处理" 两种策略，没有"自动绕过"这个取值。
4. **本层是纯函数**：不碰 DB、不碰网络（`analysis/` 的纪律也一样适用），
   所以"哪一档能被开启"能直接用测试钉住。

`Tier` 的语义（风险递增）：

| Tier | 允许做什么 | 开启门槛 |
| --- | --- | --- |
| T0 | 只影响本机数据，不产生对外动作 | 无 |
| T1 | 对外发消息（通知），无写操作 | `risk_acknowledged` |
| T2 | 无人值守写操作（自动投递） | T1 + 输入确认短语 |
| T3 | 可能直接导致封号 | T2 + 本地运行 + 独立确认短语 |
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    """风险等级。数值本身有序，闸门就是按它逐级校验。"""

    T0 = 0  #: 纯本地，无对外动作
    T1 = 1  #: 对外发消息
    T2 = 2  #: 无人值守写操作
    T3 = 3  #: 可能直接导致封号


TIER_LABELS: dict[Tier, str] = {
    Tier.T0: "本机功能",
    Tier.T1: "对外通知",
    Tier.T2: "无人值守投递",
    Tier.T3: "账号风险能力",
}

TIER_SUMMARIES: dict[Tier, str] = {
    Tier.T0: "只在你自己的电脑上读写数据，不会替你做任何对外动作。",
    Tier.T1: "会把投递情况发到你自己配置的通知渠道（如企业微信机器人）。",
    Tier.T2: "程序会自己决定投哪些岗位并直接发出去，不再逐条等你点确认。",
    Tier.T3: "多账号切换、代理、指纹这些会明显改变平台看到的行为特征，封号风险最高。",
}

#: 逐级升级时需要用户**手动输入**的确认短语。
#: 为什么不是再勾一个 checkbox：勾选会被顺手点掉，而"打一遍这句话"逼用户读一次后果。
TIER_CONFIRM_PHRASES: dict[Tier, str] = {
    Tier.T2: "我确认开启自动投递",
    Tier.T3: "我确认承担封号风险",
}

#: 验证码策略。**故意没有"自动绕过"这个取值**——那需要视觉模型且成功率不稳，
#: 失败时反而更容易把账号推向风控。这里只允许"停下"或"等人"。
CAPTCHA_MODE_CIRCUIT = "circuit"  #: 默认：熔断，停投当天
CAPTCHA_MODE_WAIT = "wait"  #: 暂停并通知用户手动完成后继续
CAPTCHA_MODES: tuple[str, ...] = (CAPTCHA_MODE_CIRCUIT, CAPTCHA_MODE_WAIT)


@dataclass(frozen=True, slots=True)
class Capability:
    """一个可开关的能力。

    `summary` 是给用户看的**后果**说明，不是功能名——用户点开关前该看到的是
    "这会让程序自己做决定"而不是"自动投递模式"。
    """

    key: str
    label: str
    tier: Tier
    summary: str
    default: bool = False
    requires: tuple[str, ...] = ()


#: 能力注册表。新增能力在这里加一行，前端与 API 自动跟着有。
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="resume_attachment",
        label="投递时附带简历附件",
        tier=Tier.T0,
        summary="在打招呼之后把简历附件发过去。不发附件时对方只能看到在线简历。",
    ),
    Capability(
        key="autofill_blacklist",
        label="识别到拒绝就自动拉黑",
        tier=Tier.T0,
        summary="从沟通记录里认出「不合适」「岗位已关闭」之类的信号，自动把公司加进黑名单，不再重复投。",
    ),
    Capability(
        key="scheduled_queue",
        label="定时生成待投队列",
        tier=Tier.T0,
        summary="按设定的时间自动抓岗位、过滤、打分并入队。**只入队，不投递**，仍需你确认。",
    ),
    Capability(
        key="notify_webhook",
        label="通知推送（企业微信 / Webhook）",
        tier=Tier.T1,
        summary="把投递结果、熔断、需要人工处理的验证码发到你自己的机器人地址。",
    ),
    Capability(
        key="auto_apply",
        label="全自动投递",
        tier=Tier.T2,
        summary="程序按打分与过滤结果自己做决定并直接投出去，不再逐条等你点「确认投递」。",
        requires=("scheduled_queue",),
    ),
    Capability(
        key="scheduled_apply",
        label="定时自动投递",
        tier=Tier.T2,
        summary="到点自动执行全自动投递。程序没在运行时不会执行（不常驻后台）。",
        requires=("auto_apply", "scheduled_queue"),
    ),
    Capability(
        key="multi_account",
        label="多账号轮换",
        tier=Tier.T3,
        summary="一个账号接近每日额度或被风控时，自动换另一个账号继续投。",
    ),
    Capability(
        key="proxy_pool",
        label="代理 IP 池",
        tier=Tier.T3,
        summary="每个账号绑定不同出口 IP。配置错误会让请求走真实 IP，所以启用前会先校验代理连通性。",
    ),
    Capability(
        key="fingerprint_spoof",
        label="浏览器指纹伪装",
        tier=Tier.T3,
        summary="让时区、语言、视口等特征与账号一致。注意：patchright 的价值是减少伪装痕迹，主动伪造不一定更好。",
    ),
)

CAPABILITIES_BY_KEY: dict[str, Capability] = {item.key: item for item in CAPABILITIES}


def capability(key: str) -> Capability:
    """按 key 取能力定义；未知 key 直接报错（不静默返回 None）。"""
    try:
        return CAPABILITIES_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"未知的投递能力：{key}") from exc


# --------------------------------------------------------------------------- #
# 用户的选择（持久化在 apply_settings 的 JSON 里）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CapabilityState:
    """用户当前的选择。整体默认 = 全部关闭 + 没确认过任何风险。"""

    enabled: dict[str, bool] = None  # type: ignore[assignment]
    risk_acknowledged: bool = False
    signed_phrases: tuple[str, ...] = ()
    run_local: bool = True
    captcha_mode: str = CAPTCHA_MODE_CIRCUIT

    def __post_init__(self) -> None:
        if self.enabled is None:
            self.enabled = {}
        if self.captcha_mode not in CAPTCHA_MODES:
            # 认不出的策略一律退回最保守的那个，而不是放行
            self.captcha_mode = CAPTCHA_MODE_CIRCUIT

    def wants(self, key: str) -> bool:
        """用户是否勾选了这个能力（不校验闸门，只看意图）。"""
        return bool(self.enabled.get(key, capability(key).default))

    def is_on(self, key: str) -> bool:
        """用户勾了**且**闸门通过 —— 这是业务代码唯一该用的判断。"""
        return self.wants(key) and gate(key, self).allowed


def default_state() -> CapabilityState:
    return CapabilityState(enabled={})


def state_from_dict(data: dict | None) -> CapabilityState:
    """从 `apply_settings` 里读回来。缺失的 key 用默认值补齐（所以老配置行为不变）。"""
    payload = data if isinstance(data, dict) else {}
    raw_enabled = payload.get("enabled")
    enabled: dict[str, bool] = {}
    if isinstance(raw_enabled, dict):
        for key, value in raw_enabled.items():
            # 只接受注册表里认识的 key：防止老版本或手改配置塞进垃圾键
            if key in CAPABILITIES_BY_KEY:
                enabled[key] = bool(value)

    phrases = payload.get("signed_phrases")
    signed = tuple(str(item) for item in phrases) if isinstance(phrases, (list, tuple)) else ()

    return CapabilityState(
        enabled=enabled,
        risk_acknowledged=bool(payload.get("risk_acknowledged", False)),
        signed_phrases=signed,
        run_local=bool(payload.get("run_local", True)),
        captcha_mode=str(payload.get("captcha_mode") or CAPTCHA_MODE_CIRCUIT),
    )


def state_to_dict(state: CapabilityState) -> dict:
    """写回 `apply_settings`。只写注册表里的 key，顺序稳定便于对比 diff。"""
    return {
        "enabled": {item.key: state.wants(item.key) for item in CAPABILITIES},
        "risk_acknowledged": bool(state.risk_acknowledged),
        "signed_phrases": list(state.signed_phrases),
        "run_local": bool(state.run_local),
        "captcha_mode": state.captcha_mode if state.captcha_mode in CAPTCHA_MODES else CAPTCHA_MODE_CIRCUIT,
    }


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class GateResult:
    allowed: bool
    reason: str = ""
    missing_phrase: str = ""

    def __bool__(self) -> bool:  # 方便直接 `if gate(...)`
        return self.allowed


def _phrase_ok(state: CapabilityState, tier: Tier) -> bool:
    phrase = TIER_CONFIRM_PHRASES.get(tier)
    return phrase is None or phrase in state.signed_phrases


def _missing_phrase(state: CapabilityState, tier: Tier) -> str:
    """这一档（含所有更低档）还差哪句确认短语。已经签过就返回空串。"""
    for level in sorted({Tier.T1, Tier.T2, Tier.T3}):
        if level > tier:
            break
        phrase = TIER_CONFIRM_PHRASES.get(level)
        if phrase is not None and phrase not in state.signed_phrases:
            return phrase
    return ""


def gate(key: str, state: CapabilityState) -> GateResult:
    """这个能力现在能不能用。

    检查顺序刻意是"从缺得最早的开始报"：用户最需要知道的是**下一步该做什么**，
    而不是一次列出五个条件。
    """
    item = capability(key)

    if not state.wants(key):
        return GateResult(allowed=False, reason=f"「{item.label}」未开启")

    # 依赖必须先满足：否则会出现"定时自动投递开了、自动投递没开"这种自相矛盾的状态
    for dependency in item.requires:
        if not state.is_on(dependency):
            return GateResult(
                allowed=False,
                reason=f"「{item.label}」需要先开启「{capability(dependency).label}」",
                missing_phrase=_missing_phrase(state, item.tier),
            )

    if item.tier >= Tier.T1 and not state.risk_acknowledged:
        return GateResult(allowed=False, reason="还没有确认风险提示", missing_phrase=_missing_phrase(state, item.tier))

    if item.tier >= Tier.T3 and not state.run_local:
        return GateResult(
            allowed=False,
            reason="这一档只能在本地运行（云端 IP 投递会被平台风控，且会把风险引到你的账号上）",
            missing_phrase=_missing_phrase(state, item.tier),
        )

    missing = _missing_phrase(state, item.tier)
    if missing:
        return GateResult(
            allowed=False,
            reason=f"还需要输入确认短语：「{missing}」",
            missing_phrase=missing,
        )

    return GateResult(allowed=True)


def refuse(key: str, state: CapabilityState) -> None:
    """业务代码里"没开就别做"的统一出口：没开就抛，附上可操作的原因。"""
    result = gate(key, state)
    if not result.allowed:
        raise CapabilityDenied(result.reason)


class CapabilityDenied(RuntimeError):
    """能力未开启（或闸门不满足）——不允许执行该动作。"""


def enable(state: CapabilityState, key: str, *, on: bool = True) -> None:
    """勾选/取消勾选，**同时把不满足的确认短语补签或清除**。

    这里刻意不"自动帮用户签短语"：勾上 T2 能力只会记下意图，
    真正的短语必须由 API 层从用户请求里拿（见 `api/apply.py`）。
    """
    capability(key)  # 未知 key 直接抛
    state.enabled[key] = on
    if not on:
        # 关掉一个能力时，把它依赖它的那些一并关掉——否则会留下互相矛盾的状态
        for item in CAPABILITIES:
            if key in item.requires:
                state.enabled[item.key] = False
