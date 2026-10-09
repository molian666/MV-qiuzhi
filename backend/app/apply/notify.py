"""通知通道：把投递结果发到用户自己的机器人（企业微信 / 通用 Webhook）。

分两层，和项目里其它地方一样：

- **内容**：`analysis/notify.py`（纯函数，可单测）；
- **发送**：这里（只做 HTTP + 配置）。

三条硬约束：

1. **通知失败绝不能打断投递**。发消息是附带能力，网络抖一下就把整轮投递停掉
   完全不可接受，所以所有异常都被收敛成"返回失败 + 原因"。
2. **默认关闭**，且属于 T1 档（要先勾风险确认）。地址由用户自己填，存在 `AppSetting` 里；
   它不是密钥，但仍不入导出（与 `app_setting` 的既有约定一致）。
3. **`trust_env=False`**：本机常有系统代理，httpx 默认读 `HTTP_PROXY` 会让请求发不出去。
   这是项目里踩过的坑（见 `config.trust_env_proxy` 的说明）。
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from ..analysis.notify import Notification, build_notification, webhook_payload
from ..models.system import AppSetting

SETTINGS_KEY = "apply_notify_settings"

#: 通知超时要短：它是附带能力，不该让投递卡住
DEFAULT_TIMEOUT_SECONDS = 8.0

CHANNEL_WECHAT = "wechat"
CHANNEL_GENERIC = "generic"

CHANNELS: tuple[str, ...] = (CHANNEL_WECHAT, CHANNEL_GENERIC)

CHANNEL_LABELS: dict[str, str] = {
    CHANNEL_WECHAT: "企业微信群机器人",
    CHANNEL_GENERIC: "通用 Webhook（POST JSON）",
}


@dataclass(slots=True)
class NotifySettings:
    """通知配置。`url` 为空表示没配——此时一律不发，而不是报错。"""

    url: str = ""
    channel: str = CHANNEL_WECHAT
    #: 哪些事件要推。默认只推"需要你行动"的两类，成功类默认**不推**——
    #: 每投一条就响一次手机会让人很快把通知关掉，反而漏掉真正重要的熔断提醒。
    on_sent: bool = False
    on_circuit: bool = True
    on_captcha: bool = True
    on_failed: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.url.strip())

    def wants(self, kind: str) -> bool:
        return {
            "sent": self.on_sent,
            "circuit": self.on_circuit,
            "captcha": self.on_captcha,
            "failed": self.on_failed,
            # 测试消息永远发（用户就是点它来验证配置的）
            "test": True,
        }.get(kind, False)


def load_notify_settings(db: Session) -> NotifySettings:
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    channel = str(data.get("channel") or CHANNEL_WECHAT)
    return NotifySettings(
        url=str(data.get("url") or ""),
        channel=channel if channel in CHANNELS else CHANNEL_WECHAT,
        on_sent=bool(data.get("on_sent", False)),
        on_circuit=bool(data.get("on_circuit", True)),
        on_captcha=bool(data.get("on_captcha", True)),
        on_failed=bool(data.get("on_failed", True)),
    )


def save_notify_settings(db: Session, settings: NotifySettings) -> NotifySettings:
    payload = {
        "url": settings.url.strip(),
        "channel": settings.channel if settings.channel in CHANNELS else CHANNEL_WECHAT,
        "on_sent": settings.on_sent,
        "on_circuit": settings.on_circuit,
        "on_captcha": settings.on_captcha,
        "on_failed": settings.on_failed,
    }
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=payload))
    else:
        row.value = payload
        db.add(row)
    db.commit()
    return load_notify_settings(db)


@dataclass(slots=True)
class NotifyResult:
    ok: bool
    detail: str = ""


def _body_for(settings: NotifySettings, notification: Notification) -> dict[str, Any]:
    if settings.channel == CHANNEL_WECHAT:
        return webhook_payload(notification)
    # 通用 webhook：给结构化字段，方便用户接自己的自动化
    return {
        "kind": notification.kind,
        "title": notification.title,
        "summary": notification.summary,
        "text": notification.body,
    }


async def send_notification(
    settings: NotifySettings,
    notification: Notification,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    trust_env: bool = False,
) -> NotifyResult:
    """把一条通知发出去。**任何失败都只返回结果，不抛异常。**

    调用方通常在投递循环里，绝不能因为通知挂了而中断投递。
    """
    if not settings.configured:
        return NotifyResult(ok=False, detail="没有配置通知地址")

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
            response = await client.post(settings.url, json=_body_for(settings, notification))
    except httpx.HTTPError as exc:
        return NotifyResult(ok=False, detail=f"通知发送失败（网络）：{exc}")
    except Exception as exc:  # noqa: BLE001 - 配置写错（非法 URL）也不能把投递带崩
        return NotifyResult(ok=False, detail=f"通知发送失败：{type(exc).__name__}: {exc}")

    if response.status_code >= 400:
        return NotifyResult(
            ok=False,
            detail=f"通知接口返回 {response.status_code}：{response.text[:200]}",
        )

    # 企业微信即使参数不对也常常回 200，真正的结果在 body 的 errcode 里
    if settings.channel == CHANNEL_WECHAT:
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return NotifyResult(ok=True, detail="已发送（响应不是 JSON，无法进一步校验）")
        errcode = data.get("errcode")
        if errcode not in (0, None):
            return NotifyResult(
                ok=False,
                detail=f"企业微信返回 errcode={errcode}：{data.get('errmsg') or ''}",
            )

    return NotifyResult(ok=True, detail="已发送")


async def notify(
    settings: NotifySettings,
    kind: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    trust_env: bool = False,
    **payload: Any,
) -> NotifyResult:
    """组装并发出一条通知；这个事件类型没开启订阅时直接跳过。"""
    if not settings.wants(kind):
        return NotifyResult(ok=False, detail=f"这个事件没有开启通知（{kind}）")
    notification = build_notification(kind, **payload)
    return await send_notification(settings, notification, timeout=timeout, trust_env=trust_env)


@dataclass(slots=True)
class _NullNotifier:
    """什么也不做的通知器，用于"能力没开启"时的占位。"""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __call__(self, kind: str, **payload: Any) -> NotifyResult:
        self.calls.append((kind, payload))
        return NotifyResult(ok=False, detail="通知能力未开启")
