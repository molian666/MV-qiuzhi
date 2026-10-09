"""`/api/client-info` 的测试（M19 手机端）。

这个端点的唯一职责是**让前端别猜**：它只报"部署形态"这个事实，不替浏览器下结论
（`isSecureContext`、麦克风权限只能在浏览器里判断）。

两类用例都保留：
- 直接调端函数 + 注入 `Settings`：覆盖各种部署组合，快且不依赖环境；
- 走一次 TestClient：证明它**真的挂到了 `/api/client-info`** 且不需要登录
  （路由写错/被认证中间件挡住，是这类"体检端点"最典型的翻车方式）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import client_info as client_info_module
from app.config import Settings, get_settings


def _settings(**overrides: object) -> Settings:
    """构造不读 .env 的设置对象（见 conftest 里关于隔离的说明）。

    `_env_file=None` 必须**显式传关键字**：pydantic-settings 对
    "塞进 `**kwargs` 字典里的 `_env_file`"不认，于是会照读 backend/.env——
    本机 .env 里 HOST=127.0.0.1，用例就永远测不到 LAN 组合（实测踩过）。
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _invoke(settings: Settings):
    """把 settings 注入端函数。

    要替换**两个模块**的 `get_settings`：端点自己用一个，`deploy.is_local_deployment()`
    用的是它自己的那个。只打端点那一个的话，`local_deployment` 会照读 backend/.env，
    用例看起来"代码不对"、其实是注入没到位（实测踩过）。
    """
    from app import deploy as deploy_module

    originals = (
        client_info_module.get_settings,
        deploy_module.get_settings,
    )
    client_info_module.get_settings = lambda: settings  # type: ignore[assignment]
    deploy_module.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        return client_info_module.client_info()
    finally:
        client_info_module.get_settings = originals[0]  # type: ignore[assignment]
        deploy_module.get_settings = originals[1]  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 部署组合
# --------------------------------------------------------------------------- #


def test_local_without_tls_still_reports_secure_context_possible() -> None:
    """绑本机 + 没配证书：loopback 本身就是安全上下文，所以"有可能"。"""
    info = _invoke(_settings(HOST="127.0.0.1", PORT=8000))
    assert info.local_deployment is True
    assert info.use_tls is False
    assert info.secure_context_possible is True


def test_lan_http_reports_mic_requires_https() -> None:
    """绑局域网 + 无 TLS：浏览器一定不给麦克风，必须让前端能提前说明白。"""
    info = _invoke(_settings(HOST="0.0.0.0", PORT=8000, AUTH_PASSWORD="x" * 12))
    assert info.local_deployment is False
    assert info.use_tls is False
    assert info.mic_requires_https is True
    # 反代（Tailscale serve 之类）会在外部终止 TLS，服务端看不出来，
    # 所以这里**不断言** secure_context_possible 为 False——只看 use_tls。
    assert info.host == "0.0.0.0"


def test_tls_deployment_reports_no_https_warning() -> None:
    settings = _settings(
        HOST="0.0.0.0",
        PORT=8443,
        AUTH_PASSWORD="x" * 12,
        SSL_CERTFILE="C:/certs/server.crt",
        SSL_KEYFILE="C:/certs/server.key",
    )
    info = _invoke(settings)
    assert info.use_tls is True
    assert info.mic_requires_https is False
    assert info.secure_context_possible is True
    assert info.port == 8443


def test_speech_provider_is_reported_for_the_frontend() -> None:
    """前端要靠它决定提示"文字替身"还是"真语音识别"。"""
    info = _invoke(_settings(SPEECH_PROVIDER="qwen"))
    assert info.speech_provider == "qwen"


def test_does_not_leak_secrets() -> None:
    """体检端点不该带出任何密钥类信息（它不需要登录，谁都能看）。"""
    settings = _settings(
        AUTH_PASSWORD="super-secret-password",
        LLM_API_KEY="sk-should-not-appear",
        DASHSCOPE_API_KEY="dashscope-should-not-appear",
    )
    payload = _invoke(settings).model_dump()
    dumped = str(payload)
    assert "super-secret-password" not in dumped
    assert "should-not-appear" not in dumped
    assert "password" not in dumped.lower()
    assert "key" not in {name.lower() for name in payload}


# --------------------------------------------------------------------------- #
# 真的挂上了吗（路由 + 无需登录）
# --------------------------------------------------------------------------- #


def test_endpoint_is_reachable_without_login(client: TestClient) -> None:
    response = client.get("/api/client-info")
    assert response.status_code == 200, response.text
    body = response.json()
    # 测试环境下 HOST 是默认的 127.0.0.1，所以应被判为本机
    assert body["local_deployment"] is True
    assert body["use_tls"] is False
    assert set(body) == {
        "secure_context_possible",
        "mic_requires_https",
        "use_tls",
        "local_deployment",
        "auth_enabled",
        "speech_provider",
        "host",
        "port",
    }


def test_endpoint_reachable_even_when_auth_enabled(client: TestClient, monkeypatch) -> None:
    """配了密码、且**未登录**时它也要能访问：用户很可能正卡在登录页排查"为什么没麦克风"。

    这条钉的是 `auth.PUBLIC_PATHS` 里必须有 `/api/client-info`——
    漏掉的话端点会返回 401，而且失败时机偏偏是最需要它的时候。
    """
    monkeypatch.setenv("AUTH_PASSWORD", "a-real-password")
    monkeypatch.setenv("AUTH_SECRET", "a-real-secret")
    get_settings.cache_clear()
    try:
        response = client.get("/api/client-info")
        assert response.status_code == 200, response.text
        assert response.json()["auth_enabled"] is True
    finally:
        get_settings.cache_clear()
