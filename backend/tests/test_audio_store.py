"""音频落盘测试：整场 + 逐回合切片，且时长与写入字节一致。"""

from __future__ import annotations

import wave
from pathlib import Path

from app.realtime.audio_store import AudioStore

from .audio_fixtures import silence, tone


def _frames(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes(), handle.getframerate(), handle.getnchannels()


def test_full_and_turn_files_are_written(tmp_path: Path) -> None:
    store = AudioStore(tmp_path / "audio", 42, sample_rate=16000)

    store.append(silence(100))
    store.begin_turn(0)
    store.append(tone(200))
    store.end_turn()
    store.begin_turn(1)
    store.append(tone(100))
    store.close()

    assert store.full_path.name == "full.wav"
    assert store.full_path.parent.name == "42"

    frames, rate, channels = _frames(store.full_path)
    assert (rate, channels) == (16000, 1)
    assert frames == 16000 * 400 // 1000  # 100 + 200 + 100 ms
    assert store.duration_ms == 400

    turn0_frames, _, _ = _frames(store.turn_path(0))
    turn1_frames, _, _ = _frames(store.turn_path(1))
    assert turn0_frames == 16000 * 200 // 1000
    assert turn1_frames == 16000 * 100 // 1000
    assert [path.name for path in store.list_turn_files()] == ["turn_000.wav", "turn_001.wav"]


def test_begin_turn_closes_previous_slice(tmp_path: Path) -> None:
    store = AudioStore(tmp_path, 1)
    store.begin_turn(0)
    store.append(tone(120))
    store.begin_turn(1)  # 没显式 end_turn 也要收好上一段
    store.append(tone(80))
    store.close()

    assert _frames(store.turn_path(0))[0] == 16000 * 120 // 1000
    assert _frames(store.turn_path(1))[0] == 16000 * 80 // 1000


def test_empty_audio_produces_no_full_file(tmp_path: Path) -> None:
    """没说过话就不该留下空文件（否则回放页会出现 0 秒音频）。"""
    store = AudioStore(tmp_path, 7)
    store.append(b"")
    store.close()
    assert not store.full_path.exists()
    assert store.duration_ms == 0


def test_duration_is_monotonic(tmp_path: Path) -> None:
    store = AudioStore(tmp_path, 9)
    seen = []
    for _ in range(5):
        store.append(tone(50))
        seen.append(store.duration_ms)
    store.close()
    assert seen == sorted(seen)
