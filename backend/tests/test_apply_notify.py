"""通知通道：内容组装（纯函数）+ 发送语义。

这一组用例**一个真实的网络请求都不发**：HTTP 客户端在测试里被替换成假的。
真发出去不仅慢，还会往用户的机器人里塞测试消息。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.analysis.notify import build_notification, webhook_payload
from app.apply.notify import (
    CHANNEL_GENERIC,
    CHANNEL_WECHAT,
    NotifySettings,
    load_notify_settings,
    notify,
    save_notify_settings,
    send_notification,
)

# --------------------------------------------------------------------------- #
# 内容组装（纯函数）
# --------------------------------------------------------------------------- #
def test_sent_notification_names_the_job_in_the_summary() -> None:
    """手机上看的是标题那一行，所以关键信息必须在摘要里，不能只写"投递通知"。"""
    item = build_notification("sent", company="某某科技", title="视觉工程师")

    assert "某某科技" in item.summary
    assert "视觉工程师" in item.summary


def test_circuit_notification_tells_the_user_to_stop_today() -> None:
    """熔断是最需要用户行动的一条：必须明说"今天别再投"。"""
    item = build_notification("circuit", detail="命中滑块验证", until="2026-09-21 00:00")

    body = item.body
    assert "今天不要再投递" in body
    assert "命中滑块验证" in body
    assert "2026-09-21" in body


def test_captcha_notification_tells_the_user_what_to_do() -> None:
    item = build_notification("captcha", detail="出现滑块")

    assert "手动完成验证" in item.body


def test_failed_notification_includes_reason_and_attempts() -> None:
    item = build_notification("failed", company="A", title="B", detail="选择器失效", attempts=2)

    assert "选择器失效" in item.body
    assert "已尝试 2 次" in item.body


def test_missing_fields_are_omitted_not_faked() -> None:
    """没有的信息就不写——不要编一个"未知公司"出来。"""
    item = build_notification("sent", company="", title="")

    assert "未知" not in item.body
    assert "None" not in item.body


def test_unknown_kind_does_not_raise() -> None:
    """通知是附带能力，不该成为新的失败点。"""
    item = build_notification("something_new", summary="随便什么")

    assert item.kind == "something_new"
    assert item.summary == "随便什么"


def test_wechat_payload_is_text_and_carries_the_title() -> None:
    payload = webhook_payload(build_notification("test"))

    assert payload["msgtype"] == "text"
    assert "通知测试" in payload["text"]["content"]
    # 不 @所有人：默认 @ 所有人会被群成员讨厌
    assert "mentioned_list" not in payload["text"]


# --------------------------------------------------------------------------- #
# 订阅开关
# --------------------------------------------------------------------------- #
def test_success_notifications_are_off_by_default() -> None:
    """默认只推"需要你行动"的几类。

    每投一条就响一次手机，用户很快会把通知整个关掉，反而漏掉熔断提醒。
    """
    settings = NotifySettings(url="https://example.com/hook")

    assert settings.wants("sent") is False
    assert settings.wants("circuit") is True
    assert settings.wants("captcha") is True
    assert settings.wants("failed") is True
    # 测试消息永远能发——用户就是点它来验证配置
    assert settings.wants("test") is True


def test_unconfigured_settings_are_not_configured() -> None:
    assert NotifySettings().configured is False
    assert NotifySettings(url="   ").configured is False
    assert NotifySettings(url="https://x").configured is True


# --------------------------------------------------------------------------- #
# 发送：用假的 HTTP 客户端
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"errcode": 0}
        self.text = text or ""

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    """记录调用、按脚本返回响应。`raises` 用来模拟网络层异常。"""

    def __init__(self, response: _FakeResponse | None = None, raises: Exception | None = None) -> None:
        self.response = response or _FakeResponse()
        self.raises = raises
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.kwargs: dict[str, Any] = {}

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any] | None = None, **kwargs: Any) -> _FakeResponse:
        self.posts.append((url, json or {}))
        if self.raises is not None:
            raise self.raises
        return self.response


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> Any:
    """替换 `httpx.AsyncClient`，返回一个装客户端的盒子。"""

    class _Box:
        client = _FakeClient()
        last_kwargs: dict[str, Any] = {}

        def install(self, client: _FakeClient) -> _FakeClient:
            self.client = client
            return client

    box = _Box()

    def factory(**kwargs: Any) -> _FakeClient:
        box.last_kwargs = kwargs
        return box.client

    monkeypatch.setattr("app.apply.notify.httpx.AsyncClient", factory)
    return box


async def test_send_posts_to_the_configured_url(fake_http: Any) -> None:
    client = fake_http.install(_FakeClient())
    settings = NotifySettings(url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc")

    result = await send_notification(settings, build_notification("test"))

    assert result.ok is True
    assert client.posts[0][0] == settings.url
    assert client.posts[0][1]["msgtype"] == "text"


async def test_send_ignores_environment_proxy(fake_http: Any) -> None:
    """本机常有系统代理，httpx 默认读 HTTP_PROXY 会让请求发不出去。"""
    fake_http.install(_FakeClient())

    await send_notification(NotifySettings(url="https://x"), build_notification("test"))

    assert fake_http.last_kwargs.get("trust_env") is False


async def test_network_error_is_returned_not_raised(fake_http: Any) -> None:
    """**最关键的一条**：通知挂在投递循环里，网络抖一下绝不能把投递打断。"""
    fake_http.install(_FakeClient(raises=httpx.ConnectError("连不上")))

    result = await send_notification(NotifySettings(url="https://x"), build_notification("test"))

    assert result.ok is False
    assert "网络" in result.detail


async def test_invalid_url_style_error_is_returned_not_raised(fake_http: Any) -> None:
    """地址写错（比如漏了 https://）会抛非 httpx 的异常，同样要收敛成结果。"""
    fake_http.install(_FakeClient(raises=ValueError("Invalid URL")))

    result = await send_notification(NotifySettings(url="not-a-url"), build_notification("test"))

    assert result.ok is False
    assert "通知发送失败" in result.detail


async def test_http_error_status_is_reported(fake_http: Any) -> None:
    fake_http.install(_FakeClient(_FakeResponse(status_code=500, text="boom")))

    result = await send_notification(NotifySettings(url="https://x"), build_notification("test"))

    assert result.ok is False
    assert "500" in result.detail


async def test_wechat_errcode_is_checked_even_on_http_200(fake_http: Any) -> None:
    """企业微信参数不对时也常回 200，真正的结果在 body 的 errcode 里。"""
    fake_http.install(
        _FakeClient(_FakeResponse(status_code=200, body={"errcode": 93000, "errmsg": "invalid webhook url"}))
    )

    result = await send_notification(
        NotifySettings(url="https://x", channel=CHANNEL_WECHAT), build_notification("test")
    )

    assert result.ok is False
    assert "93000" in result.detail


async def test_generic_channel_sends_structured_body(fake_http: Any) -> None:
    client = fake_http.install(_FakeClient(_FakeResponse(status_code=200, body={})))

    result = await send_notification(
        NotifySettings(url="https://x", channel=CHANNEL_GENERIC), build_notification("sent", company="A")
    )

    assert result.ok is True
    body = client.posts[0][1]
    assert body["kind"] == "sent"
    assert "text" in body


async def test_unconfigured_url_skips_the_request(fake_http: Any) -> None:
    client = fake_http.install(_FakeClient())

    result = await send_notification(NotifySettings(), build_notification("test"))

    assert result.ok is False
    assert "没有配置" in result.detail
    assert client.posts == [], "没配地址就不该发请求"


# --------------------------------------------------------------------------- #
# notify() 的订阅过滤
# --------------------------------------------------------------------------- #
async def test_notify_skips_unsubscribed_event(fake_http: Any) -> None:
    client = fake_http.install(_FakeClient())
    settings = NotifySettings(url="https://x", on_sent=False)

    result = await notify(settings, "sent", company="A")

    assert result.ok is False
    assert client.posts == []


async def test_notify_sends_subscribed_event(fake_http: Any) -> None:
    client = fake_http.install(_FakeClient())
    settings = NotifySettings(url="https://x", on_circuit=True)

    result = await notify(settings, "circuit", detail="滑块")

    assert result.ok is True
    assert len(client.posts) == 1


# --------------------------------------------------------------------------- #
# 配置存取
# --------------------------------------------------------------------------- #
def test_notify_settings_round_trip(client: Any) -> None:
    from app.db import session_scope

    with session_scope() as db:
        save_notify_settings(
            db,
            NotifySettings(url="https://hook", channel=CHANNEL_GENERIC, on_sent=True, on_captcha=False),
        )
        restored = load_notify_settings(db)

    assert restored.url == "https://hook"
    assert restored.channel == CHANNEL_GENERIC
    assert restored.on_sent is True
    assert restored.on_captcha is False


def test_notify_settings_default_to_off_and_wechat(client: Any) -> None:
    from app.db import session_scope

    with session_scope() as db:
        settings = load_notify_settings(db)

    assert settings.configured is False
    assert settings.channel == CHANNEL_WECHAT


def test_unknown_channel_falls_back_to_wechat(client: Any) -> None:
    from app.db import session_scope

    with session_scope() as db:
        save_notify_settings(db, NotifySettings(url="https://x", channel="made-up"))
        restored = load_notify_settings(db)

    assert restored.channel == CHANNEL_WECHAT
