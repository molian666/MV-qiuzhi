"""通知配置 API 与「投递后真的发通知」的集成。

发送被替换成假的：**测试不该真的往用户的机器人里发消息**。这里验证的是
"该发的时候发了、不该发的时候没发、地址不会泄漏"。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.apply.capabilities import CAPABILITIES
from app.apply.notify import NotifyResult

SETTINGS_URL = "/api/apply/settings"
NOTIFY_URL = "/api/apply/notify/settings"
TEST_URL = "/api/apply/notify/test"
EXECUTE_URL = "/api/apply/execute"
SEARCH_URL = "/api/apply/search"


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """拦下发通知，记录 (kind, payload)。

    **保留真实的订阅过滤**：直接替换整个 `send_notify` 会让"用户没订阅这个事件"
    这件事被绕过，于是测试变成"只要记录了就会发"，与线上行为不一致。
    所以假函数先跑一遍真实的 `NotifySettings.wants()`。
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_notify(settings: Any, kind: str, **kwargs: Any) -> NotifyResult:
        payload = {k: v for k, v in kwargs.items() if k != "trust_env"}
        if not settings.wants(kind):
            return NotifyResult(ok=False, detail=f"这个事件没有开启通知（{kind}）")
        calls.append((kind, payload))
        return NotifyResult(ok=True, detail="已发送")

    monkeypatch.setattr("app.api.apply.send_notify", fake_notify)
    return calls


def _enable(client: Any, keys: list[str], *, risk: bool = True) -> None:
    enabled = {item.key: False for item in CAPABILITIES}
    for key in keys:
        enabled[key] = True
    response = client.put(
        SETTINGS_URL,
        json={"enabled": True, "risk_acknowledged": risk, "capabilities": {"enabled": enabled}},
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def test_notify_settings_require_risk_acknowledgement(client: Any) -> None:
    """通知是对外发消息（T1），没勾风险确认不许配。"""
    response = client.put(NOTIFY_URL, json={"url": "https://example.com/hook"})

    assert response.status_code == 403
    assert "风险确认" in response.json()["detail"]


def test_notify_settings_round_trip(client: Any) -> None:
    _enable(client, ["notify_webhook"])

    response = client.put(
        NOTIFY_URL,
        json={
            "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET1234",
            "channel": "wechat",
            "on_sent": True,
            "on_captcha": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["configured"] is True
    assert payload["on_sent"] is True
    assert payload["on_captcha"] is False
    assert {item["key"] for item in payload["channels"]} == {"wechat", "generic"}


def test_notify_url_is_never_echoed_back(client: Any) -> None:
    """**地址里的 key 等同于凭据**，不能回显；只给脱敏片段。"""
    _enable(client, ["notify_webhook"])
    secret = "SECRET1234"

    client.put(
        NOTIFY_URL,
        json={"url": f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={secret}"},
    )
    payload = client.get(NOTIFY_URL).json()

    assert payload["url"] == ""
    assert secret not in str(payload)
    assert "qyapi.weixin.qq.com" in payload["masked_url"]
    assert payload["configured"] is True


def test_unknown_channel_falls_back(client: Any) -> None:
    _enable(client, ["notify_webhook"])

    payload = client.put(NOTIFY_URL, json={"url": "https://x", "channel": "made-up"}).json()
    assert payload["channel"] == "wechat"


def test_notify_defaults_when_never_configured(client: Any) -> None:
    payload = client.get(NOTIFY_URL).json()

    assert payload["configured"] is False
    assert payload["on_circuit"] is True
    assert payload["on_sent"] is False


# --------------------------------------------------------------------------- #
# 测试连通性：永远 200
# --------------------------------------------------------------------------- #
def test_test_endpoint_always_returns_200_even_on_failure(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """与 /api/system/test-llm 同一个理由：失败是正常结果，需要的是**原始错误**。"""

    async def failing(settings: Any, kind: str, **kwargs: Any) -> NotifyResult:
        return NotifyResult(ok=False, detail="errcode=93000：invalid webhook url")

    monkeypatch.setattr("app.api.apply.send_notify", failing)

    response = client.post(TEST_URL, json={"url": "https://x"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "93000" in body["detail"]


def test_test_endpoint_reports_success(client: Any, sent: list[Any]) -> None:
    response = client.post(TEST_URL, json={"url": "https://x"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert sent and sent[0][0] == "test"


# --------------------------------------------------------------------------- #
# 投递之后真的发通知
# --------------------------------------------------------------------------- #
def _queue_and_approve(client: Any) -> None:
    search = client.post(SEARCH_URL, json={"keywords": [], "limit": 5})
    assert search.status_code == 200, search.text
    task_ids = [item["task_id"] for item in search.json()["candidates"] if item["task_id"]]
    approved = client.post("/api/apply/queue/approve", json={"task_ids": task_ids})
    assert approved.status_code == 200


def test_execute_sends_no_notification_when_capability_off(client: Any, sent: list[Any]) -> None:
    """能力没开时一条都不该发——否则用户会觉得程序在偷偷往外发消息。"""
    _enable(client, [])
    client.put(NOTIFY_URL, json={"url": "https://x"})
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})
    assert response.status_code == 200
    assert sent == []


def test_execute_sends_no_notification_when_url_missing(client: Any, sent: list[Any]) -> None:
    """能力开了但没配地址：静默跳过，不报错。"""
    _enable(client, ["notify_webhook"])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})
    assert response.status_code == 200
    assert sent == []


def test_execute_notifies_on_success_when_subscribed(client: Any, sent: list[Any]) -> None:
    _enable(client, ["notify_webhook"])
    client.put(NOTIFY_URL, json={"url": "https://x", "on_sent": True, "on_captcha": False})
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})
    assert response.status_code == 200

    assert [kind for kind, _ in sent] == ["sent"]
    assert sent[0][1]["company"], "通知里要带上是哪家公司"


def test_execute_does_not_notify_success_when_not_subscribed(client: Any, sent: list[Any]) -> None:
    """成功类默认不订阅：每投一条响一次手机会让人把通知整个关掉。"""
    _enable(client, ["notify_webhook"])
    client.put(NOTIFY_URL, json={"url": "https://x", "on_sent": False})
    _queue_and_approve(client)

    client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert sent == []


def test_execute_still_succeeds_when_notification_fails(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**关键**：通知发失败不该影响投递结果，也不该让接口报错。"""

    async def exploding(settings: Any, kind: str, **kwargs: Any) -> NotifyResult:
        raise RuntimeError("通知服务炸了")

    _enable(client, ["notify_webhook"])
    client.put(NOTIFY_URL, json={"url": "https://x", "on_sent": True})
    _queue_and_approve(client)

    # 真实实现内部会把异常收敛成 NotifyResult；这里模拟一个更糟的情况（直接抛），
    # 用来确认接口层不会因此变成 500
    monkeypatch.setattr("app.api.apply.send_notify", exploding)
    try:
        response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})
    except RuntimeError:
        pytest.fail("通知抛异常时不该把投递接口带崩")

    assert response.status_code == 200
    assert response.json()["sent"] == 1
