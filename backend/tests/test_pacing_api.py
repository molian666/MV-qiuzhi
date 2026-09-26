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


def test_append_merges_recordings_and_keeps_the_same_profile(client: TestClient) -> None:
    """★ 追加到已有画像：合并录音列表、**保留 id**、名字不被动。

    保留 id 是硬要求：会话是拿 `profile_id` 引用画像的，
    换一条新记录会让所有已建的模拟面试指向旧画像。
    """
    first_rec = _make_recording(client, "某公司一面", DIALOG_CALM)
    second_rec = _make_recording(client, "某公司二面", DIALOG_PRESSING)

    profile = _make_profile(client, first_rec, "某公司面试官")
    profile_id = profile["id"]
    assert profile["recording_ids"] == [first_rec]

    appended = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [second_rec], "mode": "append", "profile_id": profile_id},
    ).json()

    assert appended["id"] == profile_id, "追加必须更新同一条记录"
    assert appended["recording_ids"] == [first_rec, second_rec], "录音列表要合并而不是替换"
    assert appended["name"] == "某公司面试官", "没传名字就保留原名，不该被悄悄改掉"
    assert appended["samples"], "追加后样本要重算出来"


def test_append_is_idempotent_for_the_same_recording(client: TestClient) -> None:
    """重复追加同一段录音不该出现两次。"""
    first_rec = _make_recording(client, "某公司一面", DIALOG_CALM)
    other = _make_recording(client, "某公司三面", DIALOG_SLOW)
    profile_id = _make_profile(client, first_rec, "面试官")["id"]

    for _ in range(2):
        client.post(
            "/api/interviewer-profiles",
            json={"recording_ids": [other], "mode": "append", "profile_id": profile_id},
        )
    detail = client.get(f"/api/interviewer-profiles/{profile_id}").json()

    assert detail["recording_ids"] == [first_rec, other]


def test_replace_swaps_recordings_but_keeps_id(client: TestClient) -> None:
    """重新生成：换掉录音列表，同样保留 id。"""
    first_rec = _make_recording(client, "某公司一面", DIALOG_CALM)
    second_rec = _make_recording(client, "某公司二面", DIALOG_PRESSING)
    profile_id = _make_profile(client, first_rec, "面试官")["id"]

    replaced = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [second_rec], "mode": "replace", "profile_id": profile_id},
    ).json()

    assert replaced["id"] == profile_id
    assert replaced["recording_ids"] == [second_rec], "重新生成要换掉，不是合并"


def test_append_reuses_stored_transcript(client: TestClient, monkeypatch) -> None:
    """★ 追加时**不重跑旧录音的转写**。

    这不是"顺手优化"而是必需：真 ASR 按量计费，每追加一次就把历史
    全部重转一遍，成本会随追加次数平方增长。
    `replace`（重新生成）则故意重转——用户要求重来。
    """
    from app import deps
    from app.providers.asr.mock import MockBatchTranscriber

    calls: list[str] = []
    real_transcribe = MockBatchTranscriber.transcribe

    async def counting_transcribe(self, *, audio_path=None, fallback_text=""):
        calls.append(fallback_text or (audio_path or ""))
        return await real_transcribe(self, audio_path=audio_path, fallback_text=fallback_text)

    monkeypatch.setattr(MockBatchTranscriber, "transcribe", counting_transcribe)
    monkeypatch.setattr(deps, "build_transcriber", lambda: MockBatchTranscriber())

    first_rec = _make_recording(client, "某公司一面", DIALOG_CALM)
    second_rec = _make_recording(client, "某公司二面", DIALOG_PRESSING)

    profile_id = _make_profile(client, first_rec, "面试官")["id"]
    first_round = len(calls)
    assert first_round == 1, f"建画像该转写一次，实际 {first_round}"

    client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [second_rec], "mode": "append", "profile_id": profile_id},
    )
    assert len(calls) - first_round == 1, (
        f"追加只该转写新录音那一次，实际多转了 {len(calls) - first_round} 次"
    )

    client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [second_rec], "mode": "replace", "profile_id": profile_id},
    )
    assert len(calls) - first_round - 1 == 1, "replace 该重转它保留的那段"


def test_append_without_profile_id_is_rejected(client: TestClient) -> None:
    """缺 profile_id 要给出可读的错误，而不是 500。"""
    recording_id = _make_recording(client, "某公司一面", DIALOG_CALM)

    response = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording_id], "mode": "append"},
    )
    assert response.status_code == 400
    assert "profile_id" in response.text


def test_append_to_unknown_profile_is_404(client: TestClient) -> None:
    recording_id = _make_recording(client, "某公司一面", DIALOG_CALM)

    response = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording_id], "mode": "append", "profile_id": 999999},
    )
    assert response.status_code == 404


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
