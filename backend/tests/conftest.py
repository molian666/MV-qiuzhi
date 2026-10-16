"""pytest 公共夹具：每个测试用独立的临时数据目录与 Mock 提供方。

**测试必须与环境无关**：`backend/.env` 是用户自己的配置，他完全可能在界面上
把语音切成 `qwen`、把模型切成 `openai`。如果测试直接吃到这些值，就会出现
"我在界面上配了一下，本地跑测试就红了"——看起来像代码坏了，实际是测试没隔离。
所以下面统一把这些键压成"未配置 / mock"。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.config import get_settings
from app.deps import reset_llm_client

#: 只压**提供方开关**——把 mock 明确设上，测试就不会去连外部服务。
#:
#: 注意这里**没有**去把 Key、地址、模型名清成空串：pydantic-settings 对
#: "空字符串环境变量"的语义含糊（有的当成未设置而回退到 `.env`），靠它隔离
#: 并不可靠。需要断言"只给我传的参数会算出什么"的用例，自己传
#: `Settings(_env_file=None, ...)`——那才是明确声明不读 `.env`。
_ENV_OVERRIDES = {
    "LLM_PROVIDER": "mock",
    "SPEECH_PROVIDER": "mock",
    "ASR_PROVIDER": "mock",
    "TRUST_ENV_PROXY": "false",
    # 部署过的 .env 里会带 AUTH_PASSWORD（上公网必须设），不压掉的话整套测试
    # 都会吃到"需要登录"而 401。这里统一压成未启用；要测鉴权的用例（test_auth.py）
    # 自己用 secured fixture 显式 setenv 再 cache_clear，互不干扰。
    "AUTH_PASSWORD": "",
    # .env 部署时通常是 HOST=0.0.0.0；无密码 + 公网绑定会让应用拒绝启动
    # （assert_safe_bind），测试统一走本机回环。
    "HOST": "127.0.0.1",
    # 部署过的 .env 带自签证书（SSL_CERTFILE/SSL_KEYFILE），不压掉的话
    # client-info 体检会报告 use_tls=True，与测试预期（未上云 = 无 TLS）不符。
    # 要测 TLS 组合的用例自己构造 Settings，不依赖这套环境变量。
    "SSL_CERTFILE": "",
    "SSL_KEYFILE": "",
    # 定时投递的后台循环会随应用启动、跑在 TestClient 自己的事件循环里。
    # 它按**真实时间**判断"到点没到点"，于是让 tick 计数、"同一天不跑两次"这类断言
    # 随跑测试的时刻而变（实测：同一份代码在不同时间跑结果不同）。
    # 这里统一压掉；要测循环的用例自己直接调 `tick()` / `run_once()`。
    "APPLY_SCHEDULER_ENABLED": "false",
}


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """所有测试都跑在干净环境里，不读用户 `.env` 里的模型与语音配置。

    autouse：不需要 `client` 的测试（比如直接构造 `Settings()` 的）
    同样会被隔离——它们正是被 `.env` 影响的那批。
    """
    for key, value in _ENV_OVERRIDES.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_llm_client()
    yield
    get_settings.cache_clear()
    reset_llm_client()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolate_env: None) -> Iterator[TestClient]:
    """隔离的应用实例：数据落在 tmp_path，模型走 Mock，不碰真实 data 目录。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # 导出接口的令牌：测试统一用 good（401 用例靠「不带令牌」触发）
    monkeypatch.setenv("EXPORT_TOKEN", "good")
    get_settings.cache_clear()
    reset_llm_client()
    db_module.reset_engine()

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
    reset_llm_client()
    db_module.reset_engine()
