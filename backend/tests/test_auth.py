"""单用户登录（部署适配）：无密码放行、有密码拦住、token 校验、WS 鉴权。

背景：项目原本假设「本地优先，无需账号体系」。要上公网就必须有人守门，
否则任何扫到端口的人都能读走简历、录音、面试记录，并盗用配置的 LLM Key。
"""

from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from app.auth import COOKIE_NAME, TOKEN_TTL_SECONDS, issue_token, verify_token
from app.config import get_settings

#: 用非 ASCII 密码：compare_digest 对 str 会抛错，必须走 bytes 编码
PASSWORD = "s3cret-密码-🔒"


@pytest.fixture()
def secured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """在同一个应用实例上打开密码登录——鉴权是运行时读配置的，不用重建 app。"""
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    return client


def _login(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


# --------------------------------------------------------------------------- #
# 未启用登录时：行为与本轮之前完全一致（本地开发零摩擦）
# --------------------------------------------------------------------------- #
def test_without_password_everything_is_open(client: TestClient) -> None:
    assert client.get("/api/auth/status").json()["auth_enabled"] is False
    assert client.get("/api/targets").status_code == 200
    assert client.get("/api/vision/meta").status_code == 200


def test_login_is_rejected_when_not_enabled(client: TestClient) -> None:
    """没配密码时不该能"登录"——否则会让人以为有防护。"""
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 400
    assert "未启用登录" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 启用后：所有 /api/* 都要令牌
# --------------------------------------------------------------------------- #
def test_every_api_endpoint_requires_login(secured: TestClient) -> None:
    for path in (
        "/api/targets",
        "/api/sessions",
        "/api/resumes",
        "/api/vision/questions",
        "/api/vision/meta",
        "/api/design/questions",
        "/api/apply/queue",
        "/api/export/preview",
        "/api/applications",
        "/api/skills/mastery",
    ):
        assert secured.get(path).status_code == 401, path


def test_public_paths_stay_open(secured: TestClient) -> None:
    """健康检查（探活）与登录状态查询必须免鉴权，否则前端没法知道要登录。"""
    assert secured.get("/api/health").status_code == 200
    status = secured.get("/api/auth/status").json()
    assert status == {"auth_enabled": True, "logged_in": False, "ttl_days": TOKEN_TTL_SECONDS // 86400}


def test_mutation_endpoints_are_locked_too(secured: TestClient) -> None:
    """不能只锁 GET——写入端点漏了等于别人能改你的简历。"""
    assert secured.post("/api/targets", json={"company": "甲", "title": "后端"}).status_code == 401
    assert secured.delete("/api/targets/1").status_code == 401
    assert secured.put("/api/apply/settings", json={}).status_code == 401


def test_login_gives_token_and_cookie(secured: TestClient) -> None:
    response = secured.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token"] and body["expires_in"] == TOKEN_TTL_SECONDS

    cookie = response.cookies.get(COOKIE_NAME)
    # cookie 是给 WebSocket 与「点链接直接下载导出」用的：
    # 浏览器的 WebSocket 构造函数不能自定义请求头，<a href> 导航也不会带 Authorization
    assert cookie == body["token"]


def test_wrong_password_is_rejected(secured: TestClient) -> None:
    for wrong in ("", "nope", PASSWORD + " "):
        assert secured.post("/api/auth/login", json={"password": wrong}).status_code == 401, wrong


def test_token_works_via_header_cookie_and_query(secured: TestClient) -> None:
    token = _login(secured)
    assert secured.get("/api/targets", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert secured.get(f"/api/targets?token={token}").status_code == 200
    # 登录响应里的 Set-Cookie 被 TestClient 收下，后续请求自动带上 → 200
    assert secured.get("/api/targets").status_code == 200


def test_status_reports_logged_in_after_login(secured: TestClient) -> None:
    _login(secured)
    assert secured.get("/api/auth/status").json()["logged_in"] is True


def test_logout_clears_cookie(secured: TestClient) -> None:
    _login(secured)
    assert secured.post("/api/auth/logout").status_code == 200
    secured.cookies.clear()  # TestClient 不会自动清，模拟浏览器丢弃
    assert secured.get("/api/targets").status_code == 401


# --------------------------------------------------------------------------- #
# token 本身的校验
# --------------------------------------------------------------------------- #
def test_token_round_trip() -> None:
    token, expires = issue_token()
    assert verify_token(token) is True
    assert expires > time.time()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-token",
        "a.b.c",
        "onlypayload.",
        ".onlysignature",
        "payload.with-wrong-signature",
    ],
)
def test_malformed_tokens_are_rejected(bad: str) -> None:
    assert verify_token(bad) is False


def test_expired_token_is_rejected(secured: TestClient) -> None:
    token, _ = issue_token(now=time.time() - TOKEN_TTL_SECONDS - 60)
    assert verify_token(token) is False
    assert secured.get("/api/targets", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_flipping_a_bit_in_the_signature_is_rejected(secured: TestClient) -> None:
    token = _login(secured)
    payload, _, signature = token.partition(".")
    flipped = signature[:-1] + ("A" if signature[-1] != "A" else "B")
    assert secured.get("/api/targets", headers={"Authorization": f"Bearer {payload}.{flipped}"}).status_code == 401


def test_forged_expiry_fails_signature(secured: TestClient) -> None:
    """把过期时间往后改必须同时改签名，而签名改不出来——这才是自签 token 的意义。"""
    token, _ = issue_token()
    payload, _, signature = token.partition(".")
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("ascii")
    version, issued, expires = raw.split(":")
    forged = f"{version}:{issued}:{int(expires) + 10**7}"
    forged_payload = base64.urlsafe_b64encode(forged.encode("ascii")).decode("ascii").rstrip("=")
    assert secured.get("/api/targets", headers={"Authorization": f"Bearer {forged_payload}.{signature}"}).status_code == 401


def test_secret_is_persisted_so_restart_does_not_log_everyone_out() -> None:
    """密钥写在 data/.auth_secret：进程重启后旧 token 仍有效。"""
    token, _ = issue_token()
    assert verify_token(token) is True
    # 清掉 settings 缓存（相当于重读配置），密钥仍从文件读回
    get_settings.cache_clear()
    assert verify_token(token) is True


# --------------------------------------------------------------------------- #
# WebSocket 也要鉴权（否则登录只是遮住了 REST）
# --------------------------------------------------------------------------- #
def test_websocket_rejects_unauthenticated(secured: TestClient) -> None:
    token = _login(secured)
    target = secured.post(
        "/api/targets", json={"company": "甲公司", "title": "后端"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    session = secured.post(
        "/api/sessions", json={"target_id": target["id"]}, headers={"Authorization": f"Bearer {token}"}
    ).json()

    from app.main import create_app

    anonymous = TestClient(create_app())
    with anonymous.websocket_connect(f"/ws/sessions/{session['id']}") as ws:
        payload = ws.receive_json()

    assert payload["type"] == "error"
    assert "未登录" in payload["message"]
