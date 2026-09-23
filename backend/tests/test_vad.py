"""能量 VAD 的行为测试。

这些断言是"回合切分"的地基：切错了，后面所有评分与表达分析都跟着错。
"""

from __future__ import annotations

from app.realtime.vad import EnergyVad, VadConfig, rms_of_pcm16

from .audio_fixtures import silence, tone


def test_rms_reflects_loudness() -> None:
    assert rms_of_pcm16(silence(20)) == 0.0
    loud = rms_of_pcm16(tone(20, amplitude=0.3))
    assert 0.15 < loud < 0.30  # 正弦 RMS ≈ 0.3/√2 ≈ 0.21
    assert rms_of_pcm16(b"") == 0.0


def test_detects_speech_then_end() -> None:
    vad = EnergyVad(VadConfig(min_silence_ms=300))
    events = vad.process(silence(200) + tone(400) + silence(400))

    kinds = [event.kind for event in events]
    assert kinds == ["speech_started", "speech_stopped"]

    started, stopped = events
    assert stopped.at_ms > started.at_ms
    # 说话时长应接近 400ms（误差不超过几帧）
    assert 340 <= stopped.speech_ms <= 460


def test_single_loud_frame_is_not_speech() -> None:
    """单帧爆音（咳嗽、鼠标声）不该被当成开口。"""
    vad = EnergyVad(VadConfig(warmup_frames=2, start_frames=3))
    events = vad.process(silence(100) + tone(20) + silence(200))
    assert events == []


def test_warmup_frames_ignore_startup_noise() -> None:
    """麦克风刚打开时的底噪不能触发开口（否则每次开麦都会误判）。"""
    noisy_startup = tone(100, amplitude=0.05) + silence(300)
    vad = EnergyVad(VadConfig(warmup_frames=5))
    assert vad.process(noisy_startup) == []


def test_short_pause_does_not_end_speech() -> None:
    """句中短暂停顿（换气）不应该结束回合。"""
    vad = EnergyVad(VadConfig(min_silence_ms=500))
    events = vad.process(tone(300) + silence(200) + tone(300))
    kinds = [event.kind for event in events]
    assert kinds.count("speech_started") == 1
    assert "speech_stopped" not in kinds


def test_max_speech_duration_forces_stop() -> None:
    """长时间不停嘴要有兜底，不能把一场面试卡死。"""
    vad = EnergyVad(VadConfig(max_speech_ms=400, min_silence_ms=10_000))
    events = vad.process(tone(900))
    stopped = [event for event in events if event.kind == "speech_stopped"]
    assert stopped and stopped[0].speech_ms >= 400


def test_adaptive_threshold_survives_noisy_room() -> None:
    """噪声底抬升后仍能识别真实说话（阈值 = max(min_energy, 噪声底 × ratio)）。"""
    vad = EnergyVad(VadConfig(min_energy=0.001, noise_ratio=3.0, min_silence_ms=300))
    events = vad.process(tone(400, amplitude=0.01) + tone(400, amplitude=0.3) + silence(400))
    kinds = [event.kind for event in events]
    assert kinds.count("speech_started") == 1
    assert "speech_stopped" in kinds


def test_frames_split_across_chunks() -> None:
    """音频分片边界不会漏帧：任意切分方式都应得到同样的判定。"""
    pcm = tone(300) + silence(400)

    whole = EnergyVad(VadConfig(min_silence_ms=300))
    whole_events = whole.process(pcm)

    chunked = EnergyVad(VadConfig(min_silence_ms=300))
    chunked_events = []
    for offset in range(0, len(pcm), 777):  # 故意用非帧对齐的切分长度
        chunked_events.extend(chunked.process(pcm[offset : offset + 777]))

    assert [event.kind for event in chunked_events] == [event.kind for event in whole_events]
    assert chunked_events[-1].speech_ms == whole_events[-1].speech_ms
