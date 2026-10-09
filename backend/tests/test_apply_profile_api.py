"""账号管理 API（多账号轮换）。

三个重点：

1. **凭据绝不回显**：代理地址里可能带账号密码，接口只给"配了没有" + 脱敏形式。
2. **删账号不牵连历史**：引用它的任务改成用默认账号，而不是拒绝删除或留下悬空引用。
3. **代理探测永远 200**，并把**出口 IP** 告诉用户——那才是他真正想知道的。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from app.apply.proxy import ProxyProbeResult
from app.db import session_scope
from app.models.apply import ApplyTask, PlatformProfile

PROFILES_URL = "/api/apply/profiles"
PROBE_URL = "/api/apply/profiles/probe"
SETTINGS_URL = "/api/apply/settings"


def _use_platform(client: Any, platform: str = "boss") -> None:
    """把平台设成与账号一致。选号按平台过滤，两边不一致会"选不到账号"。"""
    response = client.put(
        SETTINGS_URL,
        json={"enabled": True, "risk_acknowledged": True, "platform": platform},
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# 列表与新增
# --------------------------------------------------------------------------- #
def test_empty_list_is_honest(client: Any) -> None:
    payload = client.get(PROFILES_URL).json()

    assert payload["items"] == []
    assert payload["configured"] is False
    assert payload["default_daily_limit"] >= 1
    assert payload["locales"]


def test_create_and_list(client: Any) -> None:
    _use_platform(client)

    created = client.post(PROFILES_URL, json={"label": "主号", "daily_limit": 12})
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["label"] == "主号"
    assert body["daily_limit"] == 12
    # 浏览器目录自动分配，且每个账号不同
    assert body["user_data_dir"]

    listed = client.get(PROFILES_URL).json()
    assert listed["configured"] is True
    assert len(listed["items"]) == 1


def test_each_profile_gets_its_own_browser_dir(client: Any) -> None:
    """**共用目录等于共用一个登录态**，多账号就成了假的。"""
    _use_platform(client)
    first = client.post(PROFILES_URL, json={"label": "A"}).json()
    second = client.post(PROFILES_URL, json={"label": "B"}).json()

    assert first["user_data_dir"] != second["user_data_dir"]


def test_explicit_browser_dir_is_kept(client: Any) -> None:
    _use_platform(client)
    body = client.post(PROFILES_URL, json={"label": "A", "user_data_dir": "my/dir"}).json()
    assert body["user_data_dir"] == "my/dir"


def test_default_daily_limit_is_applied(client: Any) -> None:
    _use_platform(client)
    body = client.post(PROFILES_URL, json={"label": "A"}).json()
    assert body["daily_limit"] == client.get(PROFILES_URL).json()["default_daily_limit"]


# --------------------------------------------------------------------------- #
# 凭据不回显
# --------------------------------------------------------------------------- #
def test_proxy_credentials_are_never_echoed(client: Any) -> None:
    """**地址里的账号密码等同于凭据**，不能回显。"""
    _use_platform(client)
    secret = "sup3rs3cret"

    created = client.post(
        PROFILES_URL,
        json={"label": "带代理", "proxy": f"http://alice:{secret}@proxy.example.com:8080"},
    ).json()

    assert created["proxy_configured"] is True
    assert secret not in str(created)
    assert "alice" not in str(created)
    assert "proxy.example.com:8080" in created["masked_proxy"]

    # 列表里也不能泄漏
    assert secret not in str(client.get(PROFILES_URL).json())


def test_profile_without_proxy_reports_not_configured(client: Any) -> None:
    _use_platform(client)
    body = client.post(PROFILES_URL, json={"label": "直连"}).json()

    assert body["proxy_configured"] is False
    assert body["masked_proxy"] == ""
    assert body["proxy_ok"] is True  # 没配代理＝直连，是可用的


def test_broken_proxy_is_flagged(client: Any) -> None:
    _use_platform(client)
    body = client.post(PROFILES_URL, json={"label": "坏代理", "proxy": "not-a-proxy"}).json()

    assert body["proxy_configured"] is True
    assert body["proxy_ok"] is False
    assert "无法解析" in body["masked_proxy"]


# --------------------------------------------------------------------------- #
# 更新
# --------------------------------------------------------------------------- #
def test_update_changes_label_and_limit(client: Any) -> None:
    _use_platform(client)
    profile_id = client.post(PROFILES_URL, json={"label": "旧名"}).json()["id"]

    updated = client.put(
        PROFILES_URL + f"/{profile_id}",
        json={"label": "新名", "daily_limit": 3, "enabled": False},
    )
    assert updated.status_code == 200
    body = updated.json()

    assert body["label"] == "新名"
    assert body["daily_limit"] == 3
    assert body["enabled"] is False


def test_update_with_blank_dir_keeps_the_existing_one(client: Any) -> None:
    """目录留空表示"不修改"：清空它会让学生账号失去自己的登录态。"""
    _use_platform(client)
    created = client.post(PROFILES_URL, json={"label": "A"}).json()

    updated = client.put(PROFILES_URL + f"/{created['id']}", json={"label": "A2"}).json()

    assert updated["user_data_dir"] == created["user_data_dir"]


def test_update_missing_profile_is_404(client: Any) -> None:
    response = client.put(PROFILES_URL + "/99999", json={"label": "x"})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# 删除
# --------------------------------------------------------------------------- #
def test_delete_removes_the_profile(client: Any) -> None:
    _use_platform(client)
    profile_id = client.post(PROFILES_URL, json={"label": "A"}).json()["id"]

    assert client.delete(PROFILES_URL + f"/{profile_id}").status_code == 204
    assert client.get(PROFILES_URL).json()["items"] == []


def test_delete_detaches_tasks_instead_of_refusing(client: Any) -> None:
    """删账号时引用它的任务改成"用默认账号"，**不留悬空引用、也不拒绝删除**。"""
    _use_platform(client)
    profile_id = client.post(PROFILES_URL, json={"label": "A"}).json()["id"]

    with session_scope() as db:
        task = ApplyTask(status="pending", profile_id=profile_id)
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    assert client.delete(PROFILES_URL + f"/{profile_id}").status_code == 204

    with session_scope() as db:
        saved = db.get(ApplyTask, task_id)
    assert saved is not None, "任务不该被删掉"
    assert saved.profile_id is None, "引用要断开，不能指向一个不存在的账号"


def test_delete_missing_profile_is_404(client: Any) -> None:
    assert client.delete(PROFILES_URL + "/99999").status_code == 404


# --------------------------------------------------------------------------- #
# 账号列表只显示当前平台的账号
# --------------------------------------------------------------------------- #
def test_list_is_scoped_to_the_current_platform(client: Any) -> None:
    _use_platform(client, "boss")
    client.post(PROFILES_URL, json={"label": "boss 的号", "platform": "boss"})
    client.post(PROFILES_URL, json={"label": "liepin 的号", "platform": "liepin"})

    items = client.get(PROFILES_URL).json()["items"]
    assert [item["label"] for item in items] == ["boss 的号"]


# --------------------------------------------------------------------------- #
# 代理探测：永远 200 + 给出出口 IP
# --------------------------------------------------------------------------- #
def test_probe_without_an_address_is_200(client: Any) -> None:
    response = client.post(PROBE_URL, json={})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "没有填" in response.json()["detail"]


def test_probe_reports_a_bad_address_without_network(client: Any) -> None:
    """地址本身不合法时不该去发请求。"""
    response = client.post(PROBE_URL, json={"proxy": "not-a-proxy"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "协议" in body["detail"] or "端口" in body["detail"]


def test_probe_success_reports_the_exit_ip(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """**出口 IP 才是用户想知道的**（"我换成功了吗"）。"""

    async def fake_probe(raw: str, **kwargs: Any) -> ProxyProbeResult:
        return ProxyProbeResult(ok=True, exit_ip="203.0.113.7", detail="代理可用，出口 IP：203.0.113.7")

    monkeypatch.setattr("app.api.apply.probe_proxy", fake_probe)

    response = client.post(PROBE_URL, json={"proxy": "http://proxy.example.com:8080"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["exit_ip"] == "203.0.113.7"
    assert "203.0.113.7" in body["detail"]


def test_probe_failure_is_200_with_the_raw_error(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """探测失败是正常结果，用 200 + 原始错误表达（与 /system/test-llm 一致）。"""

    async def failing(raw: str, **kwargs: Any) -> ProxyProbeResult:
        return ProxyProbeResult(ok=False, detail="通过代理请求失败：ProxyError: 拒绝连接")

    monkeypatch.setattr("app.api.apply.probe_proxy", failing)

    response = client.post(PROBE_URL, json={"proxy": "http://proxy.example.com:8080"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "拒绝连接" in body["detail"]


def test_probe_can_use_a_saved_profiles_proxy(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_platform(client)
    profile_id = client.post(
        PROFILES_URL, json={"label": "A", "proxy": "http://proxy.example.com:8080"}
    ).json()["id"]

    seen: list[str] = []

    async def fake_probe(raw: str, **kwargs: Any) -> ProxyProbeResult:
        seen.append(raw)
        return ProxyProbeResult(ok=True, exit_ip="1.2.3.4", detail="ok")

    monkeypatch.setattr("app.api.apply.probe_proxy", fake_probe)

    response = client.post(PROBE_URL, json={"profile_id": profile_id})

    assert response.status_code == 200
    assert seen == ["http://proxy.example.com:8080"]


def test_probe_warns_about_a_localhost_proxy(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """指向本机的代理不会改变出口 IP——要明确告诉用户。"""

    async def fake_probe(raw: str, **kwargs: Any) -> ProxyProbeResult:
        return ProxyProbeResult(
            ok=True,
            exit_ip="127.0.0.1",
            detail="代理可用，出口 IP：127.0.0.1。代理指向本机，出口 IP 不会改变。",
            local_proxy=True,
        )

    monkeypatch.setattr("app.api.apply.probe_proxy", fake_probe)

    body = client.post(PROBE_URL, json={"proxy": "http://127.0.0.1:7890"}).json()

    assert body["local_proxy"] is True
    assert "出口 IP 不会改变" in body["detail"]
