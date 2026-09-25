"""系统配置与删除功能测试（M13 补充）。

这两块都是「运维面」的功能，测试重点与业务功能不同：

- **配置**：安全边界——密钥不能回显、不能通过界面改白名单外的键、
  写的值必须真的生效而不是只写进文件。
- **删除**：连带性——删一场面试要把回合、作答、报告、**磁盘上的录音文件**
  全都清掉，而且不能因为外键顺序写错而失败。
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import env_store

# --------------------------------------------------------------------------- #
# .env 读写（隔离到临时文件，绝不碰真实的 backend/.env）
# --------------------------------------------------------------------------- #


@pytest.fixture
def isolated_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """把 `.env` 指向临时文件。写配置会改真实文件，测试里必须隔离。"""
    env_file = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text(
        "# 这是注释，不该被写坏\nLLM_PROVIDER=mock\nDEEPSEEK_API_KEY=\nHOST=127.0.0.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_store, "ENV_PATH", env_file)
    monkeypatch.setattr(env_store, "ENV_EXAMPLE_PATH", example)

    # write_env 直接改 os.environ，测试里要恢复，免得污染其它用例
    saved = {key: os.environ.get(key) for key in env_store.EDITABLE_KEYS}
    yield env_file
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_env_created_from_example_when_missing(isolated_env) -> None:
    """`.env` 不存在时从 `.env.example` 拷一份——不该让用户为此手工建文件。"""
    assert not isolated_env.exists()
    assert env_store.read_env()["LLM_PROVIDER"] == "mock"
    assert isolated_env.exists()


def test_write_preserves_comments_and_other_keys(isolated_env) -> None:
    """只改指定键：注释和其它行要原样保留。"""
    env_store.write_env({"DEEPSEEK_API_KEY": "sk-abc"})
    text = isolated_env.read_text(encoding="utf-8")

    assert "# 这是注释，不该被写坏" in text
    assert "HOST=127.0.0.1" in text
    assert "DEEPSEEK_API_KEY=sk-abc" in text


def test_write_appends_unknown_but_allowed_key(isolated_env) -> None:
    env_store.write_env({"EXPORT_TOKEN": "token-1"})
    assert env_store.read_env()["EXPORT_TOKEN"] == "token-1"


def test_write_takes_effect_immediately(isolated_env) -> None:
    """写完之后进程内环境变量就该变了，而不是等重启。"""
    env_store.write_env({"LLM_PROVIDER": "deepseek"})
    assert os.environ["LLM_PROVIDER"] == "deepseek"


def test_write_rejects_key_outside_whitelist(isolated_env) -> None:
    """不给「任意写环境变量」的能力——那等于把 HOST、DATA_DIR 也暴露成可远程改的。"""
    for key in ("HOST", "PORT", "DATA_DIR", "SSL_KEYFILE", "PATH"):
        with pytest.raises(env_store.EnvError):
            env_store.write_env({key: "恶意值"})


def test_write_rejects_multiline_value(isolated_env) -> None:
    """值里带换行会污染 .env 的其它行。"""
    with pytest.raises(env_store.EnvError):
        env_store.write_env({"DEEPSEEK_API_KEY": "sk-1\nHOST=0.0.0.0"})


def test_secrets_are_masked_never_returned(isolated_env) -> None:
    """★ 密钥只报「配了没有」+ 末四位，绝不回显完整值。"""
    env_store.write_env({"DEEPSEEK_API_KEY": "sk-1234567890abcdef"})
    view = env_store.public_view()

    item = view["DEEPSEEK_API_KEY"]
    assert item["is_secret"] is True
    assert item["configured"] is True
    assert item["value"] == "", "密钥项的内容不能出现在返回值里"
    assert item["masked"] == "****cdef"
    assert "1234567890" not in str(item)


def test_non_secret_values_are_readable(isolated_env) -> None:
    env_store.write_env({"LLM_PROVIDER": "deepseek", "DEEPSEEK_BASE_URL": "https://x.example"})
    view = env_store.public_view()

    assert view["LLM_PROVIDER"]["value"] == "deepseek"
    assert view["LLM_PROVIDER"]["is_secret"] is False
    assert view["DEEPSEEK_BASE_URL"]["value"] == "https://x.example"


def test_unset_secret_reports_not_configured(isolated_env) -> None:
    view = env_store.public_view()
    assert view["DEEPSEEK_API_KEY"]["configured"] is False
    assert view["DEEPSEEK_API_KEY"]["masked"] == ""


def test_env_view_covers_every_editable_key() -> None:
    view = env_store.public_view()
    assert set(view) == set(env_store.EDITABLE_KEYS)
    # 每个可改的键都要有一句说明：界面上它既是标签也是占位提示，
    # 缺了就会显示成空输入框，用户不知道这里该填什么。
    missing = set(env_store.EDITABLE_KEYS) - set(env_store.KEY_NOTES)
    assert not missing, f"这些键缺说明：{sorted(missing)}"
    assert all(item["note"] for item in view.values())


# --------------------------------------------------------------------------- #
# 配置接口
# --------------------------------------------------------------------------- #


def test_settings_endpoint_hides_secrets(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-realsecret-9999")
    body = client.get("/api/system/settings").json()

    assert "sk-realsecret-9999" not in str(body)
    assert body["settings"]["DEEPSEEK_API_KEY"]["is_secret"] is True
    assert body["settings"]["DEEPSEEK_API_KEY"]["masked"] == "****9999"


def test_settings_endpoint_rejects_bad_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """即使能过鉴权，也不该能通过接口改 HOST 这类键。"""
    monkeypatch.setattr(env_store, "EDITABLE_KEYS", (*env_store.EDITABLE_KEYS,))
    response = client.put("/api/system/settings", json={"values": {"HOST": "0.0.0.0"}})
    assert response.status_code == 400
    assert "不允许" in response.text


def test_providers_reports_mock_mode(client: TestClient) -> None:
    """Mock 时必须明确告诉用户「题目是示例内容」——否则会以为功能坏了。"""
    body = client.get("/api/system/providers").json()

    assert body["llm"]["provider"] == "mock"
    assert body["llm"]["ready"] is True, "mock 是可用状态，不该报未就绪"
    assert "mock" in body["llm"]["hint"]


def test_saving_model_settings_takes_effect_without_restart(
    client: TestClient, isolated_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 界面上改完模型配置必须**立刻**生效，不能等重启。

    这里曾经有个真 bug：`write_env` 只清了 `get_settings` 的缓存，
    没清按配置建出来的 LLM 客户端单例（`deps._client_singleton`）。
    于是在界面上把提供方切成 openai、Key 与地址都填好，程序却还在用旧的
    Mock 客户端——看起来就像"配置填了不生效"。界面当时还写着"不需要重启"。

    monkeypatch 先登记这些键，测试结束后环境变量会自动还原。
    """
    from app import deps
    from app.providers.llm.mock import MockLLMClient
    from app.providers.llm.openai_compat import OpenAICompatClient

    for key in (
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_CHAT_MODEL",
        "LLM_REASONER_MODEL",
    ):
        monkeypatch.setenv(key, "")

    assert isinstance(deps.get_llm(), MockLLMClient), "默认该是 mock"

    response = client.put(
        "/api/system/settings",
        json={
            "values": {
                "LLM_PROVIDER": "openai",
                "LLM_BASE_URL": "https://api.moonshot.cn/v1",
                "LLM_API_KEY": "sk-test-key",
                "LLM_CHAT_MODEL": "moonshot-v1-8k",
            }
        },
    )
    assert response.status_code == 200

    llm = deps.get_llm()
    assert isinstance(llm, OpenAICompatClient), "保存后该换掉 Mock 客户端"
    assert llm.label == "moonshot", "地址该被读进去"
    assert llm.model_for("interview") == "moonshot-v1-8k", "模型名该被读进去"
    # 没填推理模型 → 复用对话模型，而不是留着一个不存在的默认值
    assert llm.model_for("scoring") == "moonshot-v1-8k"

    # 切回 mock 同样立刻生效
    client.put("/api/system/settings", json={"values": {"LLM_PROVIDER": "mock"}})
    assert isinstance(deps.get_llm(), MockLLMClient)


# --------------------------------------------------------------------------- #
# 通用模型配置（任意 OpenAI 兼容平台）
# --------------------------------------------------------------------------- #


def test_provider_label_reads_host() -> None:
    from app.providers.llm.openai_compat import provider_label

    assert provider_label("https://api.deepseek.com") == "deepseek"
    assert provider_label("https://dashscope.aliyuncs.com/compatible-mode/v1") == "bailian"
    assert provider_label("https://api.siliconflow.cn/v1") == "siliconflow"
    assert provider_label("https://openrouter.ai/api/v1") == "openrouter"
    # 自建网关：猜不出就用域名，不能报错——这只是个展示标签
    assert provider_label("https://api.my-gateway.example/v1") == "my-gateway"
    assert provider_label("") == "openai-compat"


def test_llm_enabled_accepts_legacy_deepseek_value() -> None:
    """老 .env 里写 LLM_PROVIDER=deepseek 的人不该因为改名而失效。"""
    from app.config import Settings

    assert Settings(llm_provider="openai").llm_enabled is True
    assert Settings(llm_provider="deepseek").llm_enabled is True
    assert Settings(llm_provider="Mock").llm_enabled is False


def test_new_llm_keys_win_over_legacy_but_legacy_still_works() -> None:
    """新键优先、旧键兜底：两种 .env 都能跑。"""
    from app.config import Settings

    # 只有旧键：按旧键生效
    legacy = Settings(
        llm_provider="deepseek",
        deepseek_api_key="sk-legacy",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_chat_model="deepseek-chat",
    )
    assert legacy.resolved_llm_api_key == "sk-legacy"
    assert legacy.resolved_llm_base_url == "https://api.deepseek.com"
    assert legacy.resolved_llm_chat_model == "deepseek-chat"

    # 新旧都填：新的说了算
    both = Settings(
        llm_provider="openai",
        llm_api_key="sk-new",
        llm_base_url="https://api.siliconflow.cn/v1",
        llm_chat_model="Qwen/Qwen3-32B",
        deepseek_api_key="sk-legacy",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_chat_model="deepseek-chat",
    )
    assert both.resolved_llm_api_key == "sk-new"
    assert both.resolved_llm_base_url == "https://api.siliconflow.cn/v1"
    assert both.resolved_llm_chat_model == "Qwen/Qwen3-32B"


def test_reasoner_model_falls_back_to_chat_model() -> None:
    """单一型号的平台（如本地 Ollama）只填一个模型名也该能跑。"""
    from app.config import Settings

    settings = Settings(
        llm_provider="openai",
        llm_api_key="sk-x",
        llm_base_url="http://localhost:11434/v1",
        llm_chat_model="qwen3:8b",
        llm_reasoner_model="",
        deepseek_reasoner_model="",
    )
    assert settings.resolved_llm_reasoner_model == "qwen3:8b"


def test_new_keys_do_not_fall_back_to_deepseek_defaults() -> None:
    """用新键但漏填某项时，不能静默打到 DeepSeek 官方地址。

    否则填了别家的 Key 却漏填 LLM_BASE_URL，会收到一个莫名其妙的 401
    （Key 跟地址不匹配）。宁可留空，由客户端给出"缺 base_url"的明确提示。
    """
    from app.config import Settings

    partial = Settings(
        llm_provider="openai",
        llm_api_key="sk-siliconflow",
        llm_chat_model="Qwen/Qwen3-32B",
    )
    assert partial.resolved_llm_base_url == "", "漏填就是漏填，不能兜底到 DeepSeek"
    assert partial.resolved_llm_reasoner_model == "Qwen/Qwen3-32B"

    # 老 .env（一个 LLM_* 都没填）仍然按旧的两档分工生效
    legacy = Settings(llm_provider="deepseek", deepseek_api_key="sk-legacy")
    assert legacy.resolved_llm_base_url == "https://api.deepseek.com"
    assert legacy.resolved_llm_reasoner_model == "deepseek-reasoner"


def test_build_llm_client_uses_configured_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """工厂要按当前配置建客户端，且不再硬编码任何厂商。"""
    from app import deps
    from app.config import Settings
    from app.providers.llm.openai_compat import OpenAICompatClient

    monkeypatch.setattr(
        "app.deps.get_settings",
        lambda: Settings(
            llm_provider="openai",
            llm_api_key="sk-x",
            llm_base_url="https://api.moonshot.cn/v1",
            llm_chat_model="moonshot-v1-8k",
        ),
    )
    client = deps.build_llm_client()
    assert isinstance(client, OpenAICompatClient)
    assert client.label == "moonshot"
    assert client.model_for("interview") == "moonshot-v1-8k"
    assert client.model_for("scoring") == "moonshot-v1-8k", "没配 reasoner 就复用 chat"


def test_missing_key_is_reported_not_swallowed() -> None:
    """没配 Key 时要给出「去哪儿配」的明确错误，而不是等网络超时。"""
    import asyncio

    from app.providers.llm.base import LLMError
    from app.providers.llm.openai_compat import OpenAICompatClient

    client = OpenAICompatClient(api_key="", base_url="https://api.deepseek.com", chat_model="x")
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        asyncio.run(client.complete(task="interview", messages=[]))


# --------------------------------------------------------------------------- #
# 删除
# --------------------------------------------------------------------------- #


def make_session(client: TestClient) -> int:
    """跑一场最短的文字面试（开场 + 一次作答 + 结束）。"""
    created = client.post(
        "/api/sessions", json={"max_questions": 2, "question_count": 2, "question_mix": {"builtin": 1}}
    ).json()
    session_id = created["id"]
    client.post(f"/api/sessions/{session_id}/answer", json={"text": "我的回答"})
    client.post(f"/api/sessions/{session_id}/finish")
    return session_id


def test_delete_session_removes_it(client: TestClient) -> None:
    session_id = make_session(client)
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204

    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert all(item["id"] != session_id for item in client.get("/api/sessions").json())


def test_delete_session_cascades(client: TestClient) -> None:
    """★ 连带清掉回合与作答——不然数据库里会留下一堆孤儿行。"""
    session_id = make_session(client)
    turns_before = len(client.get(f"/api/sessions/{session_id}").json()["turns"])
    assert turns_before > 0

    client.delete(f"/api/sessions/{session_id}")
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    # 列表里也不该再有它的痕迹
    assert client.get("/api/sessions").json() == []


def test_delete_session_removes_audio_dir(client: TestClient) -> None:
    """★ 磁盘上的录音也要删——只删数据库行会在 data 里攒孤儿文件。"""
    from app.config import get_settings

    session_id = make_session(client)
    audio_dir = get_settings().audio_dir / str(session_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "full.wav").write_bytes(b"RIFFfake")
    (audio_dir / "turn_000.wav").write_bytes(b"RIFFfake")

    result = client.delete(f"/api/sessions/{session_id}")
    assert result.status_code == 204
    assert not audio_dir.exists(), "整场录音的目录该被删掉"


def test_delete_session_404_for_unknown(client: TestClient) -> None:
    assert client.delete("/api/sessions/999999").status_code == 404


def test_delete_session_twice_is_404(client: TestClient) -> None:
    """重复删除第二次该是 404，而不是静默成功。"""
    session_id = make_session(client)
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.delete(f"/api/sessions/{session_id}").status_code == 404


def test_delete_leaves_other_sessions_alone(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    first = make_session(client)
    second = make_session(client)

    client.delete(f"/api/sessions/{first}")
    remaining = [item["id"] for item in client.get("/api/sessions").json()]
    assert remaining == [second]


def test_deletion_does_not_touch_llm_usage_log(client: TestClient) -> None:
    """用量流水独立于会话存在——删会话不该篡改成本账。"""
    session_id = make_session(client)
    before = client.get("/api/system/usage").json()["total_calls"]

    client.delete(f"/api/sessions/{session_id}")
    after = client.get("/api/system/usage").json()["total_calls"]

    assert after == before, "删除会话不该删掉 LLM 调用记录"
