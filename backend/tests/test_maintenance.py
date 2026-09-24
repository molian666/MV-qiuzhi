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
    assert all(item["note"] for key, item in view.items() if key in env_store.KEY_NOTES)


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


def test_delete_algo_attempt(client: TestClient) -> None:
    problem = client.get("/api/algo/problems/two-sum").json()
    created = client.post(
        "/api/algo/attempts",
        json={
            "problem_slug": "two-sum",
            "language": "javascript",
            "code": "function twoSum(){}",
            "cases": [{"index": 0, "passed": True}],
        },
    ).json()

    assert client.delete(f"/api/algo/attempts/{created['id']}").status_code == 204
    assert client.get(f"/api/algo/attempts/{created['id']}").status_code == 404
    assert client.get("/api/algo/attempts").json() == []
    assert problem["slug"] == "two-sum", "题库本身不该受影响"


def test_delete_algo_attempt_404(client: TestClient) -> None:
    assert client.delete("/api/algo/attempts/999999").status_code == 404


def test_delete_design_attempt(client: TestClient) -> None:
    created = client.post(
        "/api/design/attempts",
        json={
            "question_slug": "design-inline-defect-pipeline",
            "writeup": "先确认节拍与误检代价，再把成像按振动、环境光、粉尘分别应对。",
        },
    ).json()

    assert client.delete(f"/api/design/attempts/{created['id']}").status_code == 204
    assert client.get(f"/api/design/attempts/{created['id']}").status_code == 404
    assert client.get("/api/design/attempts").json() == []


def test_delete_design_attempt_404(client: TestClient) -> None:
    assert client.delete("/api/design/attempts/999999").status_code == 404


def test_deletion_does_not_touch_llm_usage_log(client: TestClient) -> None:
    """用量流水独立于会话存在——删会话不该篡改成本账。"""
    session_id = make_session(client)
    before = client.get("/api/system/usage").json()["total_calls"]

    client.delete(f"/api/sessions/{session_id}")
    after = client.get("/api/system/usage").json()["total_calls"]

    assert after == before, "删除会话不该删掉 LLM 调用记录"
