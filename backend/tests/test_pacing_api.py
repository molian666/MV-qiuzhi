"""M4 端到端：录音 → 转写段落 → 画像 → 在模拟中生效。

验收标准对应两条断言：
1. 两段风格不同的录音必须产出**两个不同的画像**；
2. 选了画像的会话，节奏策略必须真的变了（plan 与语音会话的 silence_ms）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# 风格 A：平缓，问一句答一句，没有追问
DIALOG_CALM = """[面试官] 先做个自我介绍吧
[我] 我是 5 年后端，做过订单系统
[面试官] 讲讲你的项目
[我] 我做的是订单系统重构
[面试官] 你负责哪一部分？
[我] 我负责下单链路
"""

# 风格 B：压迫，连续追问，问题偏技术细节
DIALOG_PRESSING = """[面试官] 我们直接聊技术，缓存雪崩怎么解决？
[我] 用随机过期时间
[面试官] 还有呢？
[我] 再加互斥锁
[面试官] 还有呢？
[我] 再加本地缓存
[面试官] 那穿透和击穿呢？
[我] 穿透用布隆过滤器
[面试官] 还有什么想问我的吗？
[我] 团队规模多大？
"""

# 风格 C：面试官问完等很久才等到回答（用来验证等待时长 → VAD 阈值）
DIALOG_SLOW = """00:00 面试官：讲讲你的项目
00:08 我：我做的是订单系统
00:12 面试官：那你负责哪部分？
00:20 我：我负责下单链路
"""


def _make_recording(client: TestClient, title: str, text: str) -> int:
    response = client.post("/api/recordings", json={"title": title, "raw_text": text})
    assert response.status_code == 201
    return int(response.json()["id"])


def _make_profile(client: TestClient, recording_id: int, name: str) -> dict:
    response = client.post("/api/interviewer-profiles", json={"recording_ids": [recording_id], "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_recording_then_profile_then_segments(client: TestClient) -> None:
    recording_id = _make_recording(client, "某公司一面", DIALOG_CALM)

    created = client.get(f"/api/recordings/{recording_id}").json()
    assert created["has_text"] is True
    assert created["has_audio"] is False

    profile = _make_profile(client, recording_id, "某公司一面")
    assert profile["style_tags"]
    assert profile["policy"]["silence_ms"] > 0
    assert profile["samples"], "每题都要留下节奏样本"
    assert profile["params"]["source_features"]["question_count"] >= 2

    detail = client.get(f"/api/recordings/{recording_id}").json()
    assert detail["status"] == "done"
    assert detail["segments"], "转写段落应该落库（否则画像是凭空来的）"
    assert {segment["speaker"] for segment in detail["segments"]} == {"interviewer", "candidate"}


def test_two_recordings_produce_different_profiles(client: TestClient) -> None:
    """M4 验收第 1 条：两段不同风格的录音 → 两个不同的画像。"""
    calm_id = _make_recording(client, "平缓型", DIALOG_CALM)
    pressing_id = _make_recording(client, "压迫型", DIALOG_PRESSING)

    calm = _make_profile(client, calm_id, "平缓型面试官")
    pressing = _make_profile(client, pressing_id, "压迫型面试官")

    assert calm["policy"]["max_followup_depth"] != pressing["policy"]["max_followup_depth"] or (
        calm["styles_tags"] if False else calm["style_tags"] != pressing["style_tags"]
    )
    # 追问深度与压力水平是最直观的差异
    assert pressing["params"]["source_features"]["followup_depth"] > calm["params"]["source_features"]["followup_depth"]
    assert pressing["policy"] != calm["policy"]


def test_profile_changes_session_plan_and_voice_silence(client: TestClient) -> None:
    """M4 验收第 2 条：画像在模拟中生效。"""
    recording_id = _make_recording(client, "慢节奏", DIALOG_SLOW)
    profile = _make_profile(client, recording_id, "慢节奏面试官")

    # 不选画像：plan 里没有 pacing，行为走默认
    plain = client.post("/api/sessions", json={"max_questions": 2}).json()
    assert "pacing" not in plain["plan"]

    # 选画像：plan 带上策略，且语音会话的等待时长跟着变
    with_profile = client.post(
        "/api/sessions",
        json={"max_questions": 2, "mode": "realtime", "interviewer_profile_id": profile["id"]},
    ).json()
    assert with_profile["interviewer_profile_id"] == profile["id"]
    assert with_profile["plan"]["pacing"]["silence_ms"] == profile["policy"]["silence_ms"]
    assert with_profile["plan"]["pacing"]["profile_name"] == "慢节奏面试官"

    with client.websocket_connect(f"/ws/sessions/{with_profile['id']}") as ws:
        state = ws.receive_json()
        assert state["type"] == "state"
        assert state["silence_ms"] == profile["policy"]["silence_ms"]
        assert state["silence_ms"] != 900, "选了画像就不该再用默认等待时长"


def test_profile_from_multiple_recordings_merges_samples(client: TestClient) -> None:
    """多段录音合并统计：同一个面试官的不同场次应该越学越准。"""
    first = _make_recording(client, "一面", DIALOG_CALM)
    second = _make_recording(client, "二面", DIALOG_PRESSING)

    response = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [first, second], "name": "合并画像"},
    )
    assert response.status_code == 201
    profile = response.json()

    assert sorted(profile["recording_ids"]) == sorted([first, second])
    assert profile["params"]["source_features"]["tapped_recordings"] == 2
    assert len(profile["samples"]) >= len(
        client.get(f"/api/recordings/{first}").json()["segments"]
    ) // 2  # 至少包含第一段的题


def test_profile_requires_existing_recording(client: TestClient) -> None:
    assert client.post("/api/interviewer-profiles", json={"recording_ids": [999]}).status_code == 404


def test_recording_without_transcript_fails_with_readable_error(client: TestClient) -> None:
    """只有音频、没有文稿时（Mock 转写）要给出可执行的提示，而不是 500。"""
    recording = client.post("/api/recordings", json={"title": "只有音频"}).json()
    response = client.post("/api/interviewer-profiles", json={"recording_ids": [recording["id"]]})

    assert response.status_code == 400
    assert "文稿" in response.json()["detail"]


def test_profiles_list_and_delete(client: TestClient) -> None:
    recording_id = _make_recording(client, "待删", DIALOG_CALM)
    profile = _make_profile(client, recording_id, "临时画像")

    listed = client.get("/api/interviewer-profiles").json()
    assert any(item["id"] == profile["id"] for item in listed)
    target = next(item for item in listed if item["id"] == profile["id"])
    assert target["sample_count"] > 0
    assert target["question_count"] > 0

    assert client.delete(f"/api/interviewer-profiles/{profile['id']}").status_code == 204
    assert client.get(f"/api/interviewer-profiles/{profile['id']}").status_code == 404


def test_session_rejects_unknown_profile(client: TestClient) -> None:
    response = client.post("/api/sessions", json={"interviewer_profile_id": 999})
    assert response.status_code == 404
    assert "画像" in response.json()["detail"]


def test_wrong_recording_id_in_session_and_text_still_works(client: TestClient) -> None:
    """选了画像的**文字**会话也要能正常出题（画像只是加了指令，不该破坏链路）。"""
    recording_id = _make_recording(client, "文字会话", DIALOG_CALM)
    profile = _make_profile(client, recording_id, "画像")

    created = client.post(
        "/api/sessions",
        json={"max_questions": 2, "interviewer_profile_id": profile["id"]},
    ).json()
    assert created["turns"][0]["text"]

    answered = client.post(f"/api/sessions/{created['id']}/answer", json={"text": "我是后端"}).json()
    assert answered["question"]["text"]
