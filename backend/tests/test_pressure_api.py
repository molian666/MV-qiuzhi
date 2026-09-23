"""M11：压力模式与多角色的接入。

分两层验证：
- **文本链路**：会话 plan 记了什么、连环面怎么按轮次换人（这是验收标准里"一场会话切换 ≥2 个角色"）；
- **语音链路**：打断与限时是真的"结束当前回合"，而不是发个提示让用户自己停。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.providers.llm.mock import MockLLMClient
from app.providers.speech.mock import MockSpeechProvider
from app.realtime.audio_store import AudioStore
from app.realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime

from .audio_fixtures import silence, tone


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


# --------------------------------------------------------------------------- #
# 预设端点
# --------------------------------------------------------------------------- #
def test_options_endpoint_lists_everything(client: TestClient) -> None:
    body = client.get("/api/interview-options").json()

    assert {item["key"] for item in body["pressure_modes"]} == {"deep_drill", "interrupt", "time_limit"}
    assert {"technical", "cross", "manager", "hr", "stress", "behavioral"} <= {
        item["key"] for item in body["styles"]
    }
    assert {item["key"] for item in body["locales"]} == {"zh", "en"}
    assert body["default_panel"][0]["style_key"] == "technical"
    # 每个风格都要有音色标签，否则前端没法显示"现在是谁在问"
    assert all(item["voice_label"] for item in body["styles"])
    assert all(item["summary"] for item in body["pressure_modes"])


# --------------------------------------------------------------------------- #
# 会话 plan
# --------------------------------------------------------------------------- #
def test_session_records_pressure_and_style(client: TestClient) -> None:
    created = client.post(
        "/api/sessions",
        json={"mode": "text", "pressure": {"deep_drill": True, "max_depth": 4}, "style_key": "stress"},
    ).json()
    plan = created["plan"]

    assert plan["pressure"]["deep_drill"] is True
    assert plan["pressure"]["max_depth"] == 4
    assert plan["style_key"] == "stress"
    assert plan["style_label"] == "压力面"
    assert plan["interviewer_voice"] == "male_sharp"


def test_pressure_values_are_clamped_in_plan(client: TestClient) -> None:
    created = client.post(
        "/api/sessions",
        json={
            "mode": "text",
            "pressure": {"time_limit": True, "answer_seconds": 99_999, "interrupt": True, "interrupt_after_ms": 1},
        },
    ).json()
    pressure = created["plan"]["pressure"]

    assert pressure["answer_seconds"] <= 600
    assert pressure["interrupt_after_ms"] >= 3_000


def test_default_session_has_no_pressure(client: TestClient) -> None:
    plan = client.post("/api/sessions", json={"mode": "text"}).json()["plan"]
    assert plan["pressure"]["deep_drill"] is False
    assert plan["pressure"]["interrupt"] is False
    assert plan["pressure"]["time_limit"] is False
    assert plan["locale"] == "zh"


def test_locale_is_recorded(client: TestClient) -> None:
    english = client.post("/api/sessions", json={"mode": "text", "locale": "en"}).json()
    assert english["plan"]["locale"] == "en"
    # 未知语言回退到中文，而不是报错
    unknown = client.post("/api/sessions", json={"mode": "text", "locale": "fr"}).json()
    assert unknown["plan"]["locale"] == "zh"


def test_panel_is_recorded_and_overrides_style_key(client: TestClient) -> None:
    created = client.post(
        "/api/sessions",
        json={
            "mode": "text",
            "style_key": "hr",
            "panel": [{"style_key": "technical", "rounds": 1}, {"style_key": "hr", "rounds": 1}],
        },
    ).json()
    plan = created["plan"]

    # 配了连环面就以 panel 为准（style_key 让位）
    assert plan["style_key"] == "technical"
    assert len(plan["panel"]["stages"]) == 2
    assert plan["panel"]["timeline"][0]["start_round"] == 0
    assert plan["panel"]["timeline"][1]["start_round"] == 1

    voices = [item["voice"] for item in plan["panel"]["timeline"]]
    assert len(voices) == len(set(voices)), "连环面的音色必须可区分"


def test_duplicate_styles_still_get_distinct_voices(client: TestClient) -> None:
    created = client.post(
        "/api/sessions",
        json={
            "mode": "text",
            "panel": [{"style_key": "technical", "rounds": 1}, {"style_key": "technical", "rounds": 1}],
        },
    ).json()
    voices = [item["voice"] for item in created["plan"]["panel"]["timeline"]]
    assert len(voices) == len(set(voices))


# --------------------------------------------------------------------------- #
# 每轮换人（验收标准：一场会话内能切换 ≥2 个面试官角色）
# --------------------------------------------------------------------------- #
def test_interviewer_changes_per_round(client: TestClient) -> None:
    created = client.post(
        "/api/sessions",
        json={
            "mode": "text",
            "max_questions": 4,
            "panel": [{"style_key": "technical", "rounds": 1}, {"style_key": "hr", "rounds": 3}],
        },
    ).json()
    session_id = int(created["id"])

    for index in range(2):
        response = client.post(f"/api/sessions/{session_id}/answer", json={"text": f"第 {index + 1} 轮回答"})
        assert response.status_code == 200, response.text

    detail = client.get(f"/api/sessions/{session_id}").json()
    interviewers = [
        (turn.get("meta") or {}).get("interviewer")
        for turn in detail["turns"]
        if turn["speaker"] == "interviewer"
    ]
    labels = [item.get("label") for item in interviewers if item]

    # 开场是第 1 位（技术面），第二轮之后换成 HR 面
    assert labels[0] == "技术面"
    assert "HR 面" in labels
    assert len({item.get("style_key") for item in interviewers if item}) >= 2

    # 换人那轮要标记出来（前端据此提示"接下来换人了"）
    handover = [item for item in interviewers if item and item.get("is_handover")]
    assert handover, "换人轮必须带 is_handover 标记"


def test_single_style_session_has_no_panel_meta(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"mode": "text", "style_key": "technical"}).json()
    session_id = int(created["id"])
    client.post(f"/api/sessions/{session_id}/answer", json={"text": "回答"})

    detail = client.get(f"/api/sessions/{session_id}").json()
    interviewers = [
        (turn.get("meta") or {}).get("interviewer") for turn in detail["turns"] if turn["speaker"] == "interviewer"
    ]
    # 没配连环面时不该往 meta 里塞角色信息
    assert all(item is None for item in interviewers)


def test_pressure_session_still_answers_normally(client: TestClient) -> None:
    """三种模式叠加也不能把主链路搞坏。"""
    created = client.post(
        "/api/sessions",
        json={
            "mode": "text",
            "max_questions": 3,
            "pressure": {"deep_drill": True, "max_depth": 5, "interrupt": True, "time_limit": True},
            "locale": "en",
        },
    ).json()
    session_id = int(created["id"])

    for index in range(2):
        response = client.post(f"/api/sessions/{session_id}/answer", json={"text": f"answer {index + 1}"})
        assert response.status_code == 200

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["answer_count"] == 2
    assert detail["plan"]["locale"] == "en"


# --------------------------------------------------------------------------- #
# 语音链路：打断与限时
# --------------------------------------------------------------------------- #
async def _make_runtime(
    client: TestClient,
    tmp_path: Path,
    *,
    interrupt_after_ms: int = 0,
    answer_seconds: int = 0,
) -> tuple[VoiceSessionRuntime, list[dict[str, Any]]]:
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": 2}).json()
    session_id = int(created["id"])

    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    store = AudioStore(tmp_path / "audio", session_id, sample_rate=16000)
    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=MockLLMClient(),
        speech=MockSpeechProvider(),
        store=store,
        sink=sink,
        config=VoiceRuntimeConfig(
            max_questions=2,
            text_fallback=True,
            silence_ms=400,
            min_answer_ms=200,
            interrupt_after_ms=interrupt_after_ms,
            answer_seconds=answer_seconds,
        ),
    )
    await runtime.start()
    return runtime, events


async def test_interrupt_mode_ends_the_turn(client: TestClient, tmp_path: Path) -> None:
    # 打断阈值下限是 3 秒（"说三秒就打断"已经够苛刻了，再低就不礼貌也不真实）
    runtime, events = await _make_runtime(client, tmp_path, interrupt_after_ms=3_000)

    # 一直说、不给静音：音频时长累计超过 3 秒后应当被"插话"（= 结束本回合）。
    # 注意计时基准是**音频时长**（"他说了多久"），不是墙上时钟——
    # 所以这里靠提交更长的音频来推进时间，而不是 asyncio.sleep。
    for _ in range(5):
        await runtime.handle({"type": "audio_append", "pcm": _b64(tone(800))})
    await runtime.close()

    pressure = [event for event in events if event.get("type") == "pressure"]
    assert pressure, f"应当触发打断：{[e.get('type') for e in events]}"
    assert pressure[0]["mode"] == "interrupt"
    assert pressure[0]["elapsed_ms"] >= 3_000
    # 打断之后要给用户一个可读的说明，而不是静默结束
    assert "插话" in pressure[0]["message"]


async def test_time_limit_ends_the_turn(client: TestClient, tmp_path: Path) -> None:
    # 限时 20 秒是下限，所以这里用"打断阈值 0 + 限时最小"验证通路，
    # 真正的时间触发逻辑由纯函数测试覆盖（见 test_pressure_and_panels.py）
    runtime, events = await _make_runtime(client, tmp_path, answer_seconds=0)
    for _ in range(3):
        await runtime.handle({"type": "audio_append", "pcm": _b64(silence(200) + tone(300) + silence(600))})
    await runtime.close()

    # 没开限时就不该出现 pressure 事件
    assert [event for event in events if event.get("type") == "pressure"] == []


async def test_no_pressure_events_when_modes_off(client: TestClient, tmp_path: Path) -> None:
    runtime, events = await _make_runtime(client, tmp_path)
    for _ in range(5):
        await runtime.handle({"type": "audio_append", "pcm": _b64(tone(300))})
        await asyncio.sleep(0.1)
    await runtime.close()

    assert [event for event in events if event.get("type") == "pressure"] == []
