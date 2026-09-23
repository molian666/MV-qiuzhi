"""语音会话运行时（不经 WebSocket）：音频存档、VAD 触发、文字替身、打断。"""

from __future__ import annotations

import base64
import wave
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.providers.llm.mock import MockLLMClient
from app.providers.speech.mock import MockSpeechProvider
from app.realtime.audio_store import AudioStore
from app.realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime

from .audio_fixtures import silence, tone


async def _make_runtime(
    client: TestClient,
    tmp_path: Path,
    *,
    max_questions: int = 2,
) -> tuple[VoiceSessionRuntime, AudioStore, list[dict[str, Any]], int]:
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": max_questions}).json()
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
            max_questions=max_questions,
            text_fallback=True,
            silence_ms=300,
            min_answer_ms=200,
        ),
    )
    await runtime.start()
    return runtime, store, events, session_id


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


async def test_audio_is_stored_and_vad_asks_for_text(client: TestClient, tmp_path: Path) -> None:
    runtime, store, events, _ = await _make_runtime(client, tmp_path)

    # 预热 → 说话 500ms → 静音 600ms（> silence_ms=300，判定说完）
    pcm = silence(200) + tone(500) + silence(600)
    await runtime.handle({"type": "audio_append", "pcm": _b64(pcm)})
    await runtime.close()

    kinds = [event["type"] for event in events]
    assert "barge_in" in kinds  # 用户开口 → 通知前端停掉面试官的声音
    assert "speech_state" in kinds
    assert "awaiting_text" in kinds  # Mock 语音：提示改用文字提交
    assert "metrics" in kinds

    # 音频（含整场与逐回合）真的落盘了
    assert store.full_path.exists()
    with wave.open(str(store.full_path), "rb") as handle:
        assert (handle.getframerate(), handle.getnchannels()) == (16000, 1)
        assert handle.getnframes() == 16000 * (200 + 500 + 600) // 1000
    assert store.list_turn_files(), "应该留下逐回合切片供回放"


async def test_hint_is_not_a_turn(client: TestClient, tmp_path: Path) -> None:
    """太短的"说话"（噪音/咳嗽）不该触发回合，也不该提示文字。"""
    runtime, _, events, _ = await _make_runtime(client, tmp_path)
    await runtime.handle({"type": "audio_append", "pcm": _b64(silence(200) + tone(60) + silence(600))})
    await runtime.close()

    kinds = [event["type"] for event in events]
    assert "awaiting_text" not in kinds


async def test_text_answers_complete_interview(client: TestClient, tmp_path: Path) -> None:
    runtime, _, events, session_id = await _make_runtime(client, tmp_path, max_questions=2)

    await runtime.handle({"type": "text", "text": "第一轮：我做订单系统重构。"})
    await runtime.handle({"type": "text", "text": "第二轮：用了预扣加异步对账。"})
    await runtime.close()

    kinds = [event["type"] for event in events]
    assert kinds.count("candidate_turn") == 2
    assert kinds.count("interviewer_text") == 2
    assert "finished" in kinds

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["status"] == "finished"
    assert [turn["speaker"] for turn in detail["turns"]] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert detail["turns"][-1]["kind"] == "closing"


async def test_interrupt_is_acknowledged(client: TestClient, tmp_path: Path) -> None:
    runtime, _, events, _ = await _make_runtime(client, tmp_path)
    await runtime.handle({"type": "interrupt"})
    await runtime.close()
    assert any(event["type"] == "barge_in" and event.get("ack") for event in events)


async def test_llm_usage_is_recorded_per_turn(client: TestClient, tmp_path: Path) -> None:
    runtime, _, _, _ = await _make_runtime(client, tmp_path, max_questions=2)
    await runtime.handle({"type": "text", "text": "回答"})
    await runtime.close()

    usage = client.get("/api/system/usage").json()
    assert usage["total_calls"] >= 1
    assert usage["failed_calls"] == 0


async def test_audio_during_busy_is_still_stored(client: TestClient, tmp_path: Path) -> None:
    """出题期间麦克风仍在推流时，音频不能丢（否则录音会断片）。"""
    runtime, store, _, _ = await _make_runtime(client, tmp_path)
    runtime._busy = True  # 模拟"正在出题"
    try:
        await runtime.handle({"type": "audio_append", "pcm": _b64(tone(100))})
    finally:
        runtime._busy = False
    await runtime.close()

    assert store.duration_ms == 100
    assert store.full_path.exists()


async def test_turn_slice_actually_contains_the_audio(client: TestClient, tmp_path: Path) -> None:
    """回归：回合切片必须真的有语音。

    曾经因为"先把整块音频写盘、再处理 VAD"的顺序问题，
    `begin_turn` 发生时音频早就写完 full.wav 了，于是 turn_*.wav 只有 WAV 头（44 字节）。
    """
    runtime, store, _, _ = await _make_runtime(client, tmp_path)
    await runtime.handle({"type": "audio_append", "pcm": _b64(silence(200) + tone(900) + silence(600))})
    await runtime.close()

    slices = store.list_turn_files()
    assert len(slices) == 1

    with wave.open(str(slices[0]), "rb") as handle:
        frames = handle.getnframes()
    expected_min = 16000 * 900 // 1000 - 16000 * 20 // 1000  # 语音 900ms（允许一帧误差）
    assert frames >= expected_min, f"切片只有 {frames} 帧，语音没被写进去"


async def test_only_the_matching_answer_carries_audio(client: TestClient, tmp_path: Path) -> None:
    """回归：第二个回答不能复用第一段的录音。"""
    runtime, _, _, session_id = await _make_runtime(client, tmp_path, max_questions=2)

    # 录一段（Mock 语音没有识别，会提示用文字提交），再用文字作答
    await runtime.handle({"type": "audio_append", "pcm": _b64(silence(200) + tone(900) + silence(600))})
    await runtime.handle({"type": "text", "text": "第一轮回答（对应上面那段录音）"})
    await runtime.handle({"type": "text", "text": "第二轮回答（纯文字，没有录音）"})
    await runtime.close()

    detail = client.get(f"/api/sessions/{session_id}").json()
    answers = [turn for turn in detail["turns"] if turn["speaker"] == "candidate"]
    assert len(answers) == 2
    assert answers[0]["audio_url"] is not None
    assert answers[1]["audio_url"] is None


async def test_turn_span_is_speech_not_silence(client: TestClient, tmp_path: Path) -> None:
    """start_ms/end_ms 记录语音区间本身，不含尾部静音。

    否则每个回答都会平白多出一次静音阈值（默认 900ms），
    M9 的语速/停顿统计就全偏了。
    """
    runtime, _, _, session_id = await _make_runtime(client, tmp_path)
    await runtime.handle({"type": "audio_append", "pcm": _b64(silence(200) + tone(900) + silence(600))})
    await runtime.handle({"type": "text", "text": "回答"})
    await runtime.close()

    detail = client.get(f"/api/sessions/{session_id}").json()
    answer = next(turn for turn in detail["turns"] if turn["speaker"] == "candidate")
    span = answer["end_ms"] - answer["start_ms"]
    assert 820 <= span <= 980, f"语音区间应为约 900ms，实际 {span}ms"
