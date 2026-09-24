"""部署守卫：安全启动闸、本机专属功能的分流、前端静态托管。

用户选定「除投递执行与浏览器扩展外，全部上云」，这个文件钉住那条分界线。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deploy import is_local_deployment


@pytest.fixture()
def fresh_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """按当前环境变量重建应用（比 client 夹具更灵活：可以改 HOST / STATIC_DIR）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EXPORT_TOKEN", "good")
    get_settings.cache_clear()

    from app.main import create_app

    def build() -> TestClient:
        return TestClient(create_app())

    return build


# --------------------------------------------------------------------------- #
# 安全启动闸：上公网却没配密码 → 拒绝启动
# --------------------------------------------------------------------------- #
def test_public_bind_without_password_refuses_to_start(fresh_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError) as excinfo:
        fresh_app()

    message = str(excinfo.value)
    # 报错必须能指导下一步动作，而不只是"失败了"
    assert "拒绝启动" in message
    assert "AUTH_PASSWORD" in message
    assert "127.0.0.1" in message


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_public_hosts_all_trip_the_guard(fresh_app, monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("HOST", host)
    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        fresh_app()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_without_password_are_fine(
    fresh_app, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """本地开发不该因为加了这道闸而变麻烦。"""
    monkeypatch.setenv("HOST", host)
    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()
    with fresh_app() as client:
        assert client.get("/api/health").status_code == 200


def test_public_bind_with_password_starts(fresh_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("AUTH_PASSWORD", "强密码")
    get_settings.cache_clear()
    with fresh_app() as client:
        # 起来了，但没令牌就进不去任何 API
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/targets").status_code == 401


# --------------------------------------------------------------------------- #
# 本机专属功能：非本机运行时 403 并说清原因
# --------------------------------------------------------------------------- #
def test_local_deployment_is_detected_by_default(fresh_app) -> None:
    with fresh_app():
        assert is_local_deployment() is True


#: 云上场景必须同时有密码（否则安全闸直接拒绝启动，根本到不了投递守卫）
CLOUD_PASSWORD = "云上强密码"


def _login_cloud(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"password": CLOUD_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_apply_search_and_execute_are_blocked_when_not_local(fresh_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("AUTH_PASSWORD", CLOUD_PASSWORD)
    get_settings.cache_clear()

    with fresh_app() as client:
        assert is_local_deployment() is False
        headers = _login_cloud(client)

        search = client.post("/api/apply/search", json={"platform": "boss", "keyword": "后端"}, headers=headers)
        assert search.status_code == 403
        assert "只能在运行本机时使用" in search.json()["detail"]

        execute = client.post("/api/apply/execute", json={"task_ids": [1]}, headers=headers)
        assert execute.status_code == 403

        ingest = client.post("/api/applications/ingest", json={"platform": "boss", "rows": []}, headers=headers)
        assert ingest.status_code == 403


def test_read_only_apply_endpoints_stay_usable_in_cloud(fresh_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """云端仍要能看队列、规则与统计——只挡"动作"，不挡"查看"。"""
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("AUTH_PASSWORD", CLOUD_PASSWORD)
    get_settings.cache_clear()

    with fresh_app() as client:
        headers = _login_cloud(client)
        assert client.get("/api/apply/queue", headers=headers).status_code == 200
        assert client.get("/api/apply/rules", headers=headers).status_code == 200
        assert client.get("/api/apply/settings", headers=headers).status_code == 200
        assert client.get("/api/applications", headers=headers).status_code == 200


def test_block_message_explains_the_real_reason(fresh_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("AUTH_PASSWORD", CLOUD_PASSWORD)
    get_settings.cache_clear()

    with fresh_app() as client:
        headers = _login_cloud(client)
        detail = client.post("/api/apply/execute", json={"task_ids": [1]}, headers=headers).json()["detail"]

    # 说清"为什么"，而不只是"不允许"——否则用户会以为是 bug
    assert "IP" in detail or "风控" in detail
    assert "本机" in detail


# --------------------------------------------------------------------------- #
# 前端静态托管：一个端口 + 一张证书
# --------------------------------------------------------------------------- #
def _make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>mianshi-spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('mianshi')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def test_frontend_is_served_with_spa_fallback(fresh_app, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STATIC_DIR", str(_make_dist(tmp_path)))
    get_settings.cache_clear()

    with fresh_app() as client:
        assert "mianshi-spa" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/favicon.svg").status_code == 200
        # 深层路径落回 index.html（刷新页面不会 404）
        assert "mianshi-spa" in client.get("/career/anything").text


def test_api_namespace_is_not_swallowed_by_spa(fresh_app, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """未定义的 /api/* 必须 404，不能拿 index.html 顶替——
    否则前端会把 HTML 当 JSON 解析，报一堆看不懂的错。"""
    monkeypatch.setenv("STATIC_DIR", str(_make_dist(tmp_path)))
    get_settings.cache_clear()

    with fresh_app() as client:
        assert client.get("/api/definitely-not-here").status_code == 404
        assert client.get("/api/health").status_code == 200
        assert client.get("/ws/not-a-socket").status_code == 404


def test_no_static_dir_means_api_only(fresh_app, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """找不到 dist 也不能崩：开发时前端由 vite 提供。"""
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "nope"))
    get_settings.cache_clear()

    with fresh_app() as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404
