"""表达体检：填充词分级、语速窗口分摊、停顿切分、音量事件、紧张度、样本量保护。

这些判定会被用来告诉用户"你哪一句说快了、哪儿卡了两秒"，
所以每一条都要有测试钉住——错误的定位比没有定位更糟。
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

import pytest

from app.analysis.speech_features import (
    FILLER_RATE_WARN,
    MIN_CHARS_FOR_RATE,
    TurnSpan,
    analyze,
    count_fillers,
    detect_pauses,
    expression_score,
    filler_density,
    pace_variability,
    pace_windows,
    tension_score,
    volume_envelope_stats,
)
from app.expression_service import rms_envelope


def _turn(index: int = 0, *, speaker: str = "candidate", text: str = "", start_ms: int = 0, end_ms: int = 0) -> TurnSpan:
    return TurnSpan(index=index, speaker=speaker, text=text, start_ms=start_ms, end_ms=end_ms)


# --------------------------------------------------------------------------- #
# 填充词
# --------------------------------------------------------------------------- #
def test_strong_fillers_weigh_more_than_weak_ones() -> None:
    hits = {item.word: item for item in count_fillers("嗯，然后我们就那样做了")}
    assert hits["嗯"].strong is True
    assert hits["然后"].strong is False
    # 一个「嗯」比一个「然后」更值得改
    assert hits["嗯"].weight > hits["然后"].weight


def test_count_fillers_is_sorted_by_weighted_count() -> None:
    hits = count_fillers("嗯嗯嗯，然后然后")
    assert hits[0].word == "嗯"


def test_filler_density_is_per_thousand_chars() -> None:
    assert filler_density(10, 1000) == 10.0
    assert filler_density(10, 500) == 20.0
    assert filler_density(10, 0) == 0.0


def test_count_fillers_empty_text() -> None:
    assert count_fillers("") == []


# --------------------------------------------------------------------------- #
# 语速
# --------------------------------------------------------------------------- #
def test_pace_windows_split_answer_proportionally() -> None:
    # 100 字说了 60 秒 → 100 字/分钟；每个 10 秒窗口应分到约 16.7 字
    turns = [_turn(text="字" * 100, start_ms=0, end_ms=60_000)]
    windows = pace_windows(turns, window_ms=10_000)

    assert len(windows) == 6
    assert all(window.cpm == pytest.approx(100, abs=1) for window in windows)
    assert sum(window.chars for window in windows) == pytest.approx(100, abs=0.1)


def test_pace_windows_skip_interviewer() -> None:
    turns = [
        _turn(index=0, speaker="interviewer", text="问了一大段问题", start_ms=0, end_ms=5000),
        _turn(index=1, text="答", start_ms=5000, end_ms=10_000),
    ]
    windows = pace_windows(turns, window_ms=10_000)
    # 只统计候选人的发言时间，窗口从候选人的第一句开始
    assert windows[0].start_ms == 5000


def test_pace_variability_detects_uneven_speaking() -> None:
    steady = pace_windows([_turn(text="字" * 60, start_ms=0, end_ms=30_000)], window_ms=10_000)
    assert pace_variability(steady) == pytest.approx(0.0, abs=0.01)

    uneven = [
        _turn(index=0, text="字" * 80, start_ms=0, end_ms=10_000),
        _turn(index=1, text="字" * 10, start_ms=10_000, end_ms=20_000),
    ]
    assert pace_variability(pace_windows(uneven, window_ms=10_000)) > 0.35


def test_text_mode_without_timestamps_has_no_pace() -> None:
    """文字面试的回合没有音频时间戳——不能拿几毫秒除出一个假语速。

    这是冒烟里实测出来的：文字面试报出过 149620 字/分钟。
    """
    turns = [
        TurnSpan(index=0, speaker="candidate", text="这是一段回答" * 10, start_ms=0, end_ms=0),
        TurnSpan(index=1, speaker="candidate", text="这是另一段回答" * 10, start_ms=100, end_ms=179),
    ]
    report = analyze(turns)

    assert report.chars > 0
    assert report.overall_cpm == 0.0
    assert report.score == 0.0  # 没有时长就不能评表达分
    assert report.windows == []


def test_overall_pace_counts_only_speaking_time() -> None:
    turns = [
        _turn(index=0, text="字" * 120, start_ms=0, end_ms=60_000),
        _turn(index=1, speaker="interviewer", text="问", start_ms=60_000, end_ms=120_000),
    ]
    report = analyze(turns)
    assert report.chars == 120
    assert report.speaking_ms == 60_000
    assert report.overall_cpm == 120.0


# --------------------------------------------------------------------------- #
# 停顿
# --------------------------------------------------------------------------- #
def test_detect_pauses_finds_silence_inside_a_turn() -> None:
    volume = [0.5] * 20 + [0.0] * 40 + [0.5] * 20  # 每帧 50ms → 中间静音 2000ms
    turn = _turn(text="前半段内容后半段内容", start_ms=0, end_ms=4000)

    pauses = detect_pauses([turn], volume, frame_ms=50)
    assert len(pauses) == 1
    assert pauses[0].duration_ms == 2000
    assert pauses[0].start_ms == 1000
    assert pauses[0].kind == "long"


def test_pauses_only_look_at_candidate_turns() -> None:
    # 回合之间的空隙是面试官在说话，不是你在卡壳
    volume = [0.0] * 80
    interviewer = _turn(index=0, speaker="interviewer", text="面试官说话", start_ms=0, end_ms=4000)
    assert detect_pauses([interviewer], volume, frame_ms=50) == []


def test_pause_kind_grading() -> None:
    short_gap = [0.5] * 10 + [0.0] * 14 + [0.5] * 10  # 700ms
    long_gap = [0.5] * 10 + [0.0] * 40 + [0.5] * 10  # 2000ms
    very_long_gap = [0.5] * 10 + [0.0] * 80 + [0.5] * 10  # 4000ms
    turn = _turn(text="内容内容内容", start_ms=0, end_ms=4000)

    assert detect_pauses([turn], short_gap, frame_ms=50)[0].kind == "normal"
    assert detect_pauses([turn], long_gap, frame_ms=50)[0].kind == "long"
    assert detect_pauses([turn], very_long_gap, frame_ms=50)[0].kind == "very_long"


def test_pause_too_short_is_ignored() -> None:
    volume = [0.5] * 10 + [0.0] * 6 + [0.5] * 10  # 300ms < 600ms
    assert detect_pauses([_turn(text="内容内容", start_ms=0, end_ms=2000)], volume, frame_ms=50) == []


def test_no_volume_means_no_pauses() -> None:
    assert detect_pauses([_turn(text="内容", start_ms=0, end_ms=2000)], None) == []
    assert detect_pauses([_turn(text="内容", start_ms=0, end_ms=2000)], []) == []


def test_pause_keeps_surrounding_text_for_replay() -> None:
    volume = [0.5] * 20 + [0.0] * 40 + [0.5] * 20
    turn = _turn(text="我觉得这块主要是缓存穿透的问题需要加布隆过滤器", start_ms=0, end_ms=4000)
    pause = detect_pauses([turn], volume, frame_ms=50)[0]
    # 停顿前后各截一段，回听时知道该听哪里
    assert pause.before_text or pause.after_text


# --------------------------------------------------------------------------- #
# 音量
# --------------------------------------------------------------------------- #
def test_volume_envelope_halves() -> None:
    volume = [0.5] * 40 + [0.2] * 40
    turn = _turn(text="前半段说得比较响后半段越来越小声", start_ms=0, end_ms=4000)

    cv, mean, halves = volume_envelope_stats([turn], volume, frame_ms=50)
    assert cv > 0
    assert mean == pytest.approx(0.35, abs=0.01)
    assert len(halves) == 1
    index, front, back = halves[0]
    assert front > back


def test_volume_drop_is_flagged_as_issue() -> None:
    volume = [0.5] * 40 + [0.2] * 40
    turn = _turn(text="这段话说得比较响但结尾越来越小声了要练", start_ms=0, end_ms=4000)
    report = analyze([turn], volume=volume, frame_ms=50)
    assert any(item.kind == "volume_drop" for item in report.issues)


def test_volume_spike_is_flagged_as_issue() -> None:
    volume = [0.2] * 40 + [0.6] * 40
    turn = _turn(text="前半段很平静后半段突然提高音量说了下去", start_ms=0, end_ms=4000)
    report = analyze([turn], volume=volume, frame_ms=50)
    assert any(item.kind == "volume_spike" for item in report.issues)


# --------------------------------------------------------------------------- #
# 问题定位
# --------------------------------------------------------------------------- #
def test_issues_carry_replay_positions() -> None:
    # 前半段 10 秒说得飞快（120 字），后半段 10 秒很慢（10 字）
    turns = [
        _turn(index=0, text="字" * 120, start_ms=0, end_ms=10_000),
        _turn(index=1, text="字" * 10, start_ms=10_000, end_ms=20_000),
    ]
    report = analyze(turns)

    fast = [item for item in report.issues if item.kind == "pace_fast"]
    slow = [item for item in report.issues if item.kind == "pace_slow"]
    assert fast and slow
    # 每条问题都带时间点与回合号，前端才能把录音跳到那一句
    assert fast[0].at_ms == 0
    assert fast[0].turn_index == 0
    assert slow[0].at_ms == 10_000
    assert slow[0].turn_index == 1


def test_detect_pauses_positions_are_absolute_ms() -> None:
    # 时间线：0–12 秒在说（帧 0–239）、12–14 秒停顿（帧 240–279）、之后接着说
    volume = [0.5] * 240 + [0.0] * 40 + [0.5] * 40
    turn = _turn(text="内容" * 20, start_ms=10_000, end_ms=16_000)

    pause = detect_pauses([turn], volume, frame_ms=50)[0]
    # 停顿位置是会话内的绝对时间（不是相对这句发言的偏移）
    assert pause.start_ms == 12_000
    assert pause.duration_ms == 2000


# --------------------------------------------------------------------------- #
# 评分与紧张度
# --------------------------------------------------------------------------- #
def test_tension_grows_with_instability() -> None:
    calm = tension_score(
        pace_cv=0.02,
        volume_cv=0.05,
        pause_count=0,
        very_long_pauses=0,
        filler_per_kchar=0.0,
        speaking_minutes=5,
    )
    tense = tension_score(
        pace_cv=0.6,
        volume_cv=0.9,
        pause_count=20,
        very_long_pauses=6,
        filler_per_kchar=20.0,
        speaking_minutes=5,
    )
    assert calm < 20
    assert tense > 60


def test_tension_is_bounded() -> None:
    score = tension_score(
        pace_cv=99,
        volume_cv=99,
        pause_count=999,
        very_long_pauses=999,
        filler_per_kchar=999,
        speaking_minutes=0.1,
    )
    assert 0 <= score <= 100


@pytest.mark.parametrize("cpm", [100.0, 250.0, 450.0])
def test_expression_score_is_bounded(cpm: float) -> None:
    score = expression_score(
        overall_cpm=cpm,
        pace_cv=0.3,
        filler_per_kchar=10.0,
        very_long_pauses=3,
        speaking_minutes=5,
        has_audio=True,
    )
    assert 0 <= score <= 10


def test_comfortable_pace_scores_better() -> None:
    calm = expression_score(
        overall_cpm=230.0,
        pace_cv=0.05,
        filler_per_kchar=0.0,
        very_long_pauses=0,
        speaking_minutes=5,
        has_audio=True,
    )
    rushed = expression_score(
        overall_cpm=430.0,
        pace_cv=0.05,
        filler_per_kchar=0.0,
        very_long_pauses=0,
        speaking_minutes=5,
        has_audio=True,
    )
    assert calm > rushed


# --------------------------------------------------------------------------- #
# 样本量保护（这一条是实测踩出来的：33 字里 4 个填充词 → 每千字 121 个）
# --------------------------------------------------------------------------- #
def test_small_sample_is_flagged_and_excluded_from_scoring() -> None:
    turns = [_turn(text="嗯那个就是说这个我觉得吧", start_ms=0, end_ms=3000)]
    report = analyze(turns)

    assert report.reliable is False
    assert report.chars < MIN_CHARS_FOR_RATE
    # 密度照实计算并展示，但要在结论里说清楚
    assert report.filler_per_kchar > FILLER_RATE_WARN
    assert "仅供参考" in report.summary
    assert "填充词" in report.summary


def test_large_sample_is_reliable() -> None:
    text = "这是一段足够长的回答内容" * 30  # 360 字
    turns = [_turn(text=text, start_ms=0, end_ms=120_000)]  # 2 分钟
    report = analyze(turns)

    assert report.reliable is True
    assert report.chars >= MIN_CHARS_FOR_RATE
    assert "仅供参考" not in report.summary


def test_reliable_density_drives_suggestions() -> None:
    # 360 字里塞 12 个强填充词 → 每千字 40 个，必须给出练习建议
    text = ("嗯" + "这是一段足够长的回答内容" * 3) * 12
    turns = [_turn(text=text, start_ms=0, end_ms=120_000)]
    report = analyze(turns)

    assert report.reliable is True
    assert report.filler_per_kchar > FILLER_RATE_WARN
    assert any("填充词" in tip for tip in report.suggestions)


# --------------------------------------------------------------------------- #
# 降级：没有音频
# --------------------------------------------------------------------------- #
def test_without_audio_reports_text_level_only() -> None:
    text = "这是一段足够长的回答内容" * 30
    turns = [_turn(text=text, start_ms=0, end_ms=120_000)]
    report = analyze(turns)

    assert report.has_audio is False
    assert report.pauses == []
    assert report.volume_cv == 0.0
    assert any("语音面试" in tip for tip in report.suggestions)


def test_empty_turns_are_handled() -> None:
    report = analyze([])
    assert report.chars == 0
    assert "没有你的发言" in report.summary

    interviewer_only = analyze([_turn(speaker="interviewer", text="问问题", start_ms=0, end_ms=1000)])
    assert interviewer_only.chars == 0


# --------------------------------------------------------------------------- #
# WAV 读取
# --------------------------------------------------------------------------- #
def _write_wav(path: Path, segments: list[tuple[float, float]], *, rate: int = 16000, width: int = 2) -> None:
    """segments: [(秒数, 振幅 0-1)]；振幅 0 表示静音。"""
    samples = array.array("h")
    for seconds, amplitude in segments:
        for index in range(int(rate * seconds)):
            value = int(amplitude * 32767 * math.sin(2 * math.pi * 220 * index / rate))
            samples.append(value)

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())


def test_rms_envelope_reads_wav(tmp_path: Path) -> None:
    path = tmp_path / "full.wav"
    _write_wav(path, [(1.0, 0.5), (2.0, 0.0), (1.0, 0.5)])

    envelope = rms_envelope(path, frame_ms=50)
    assert len(envelope) == 80
    assert envelope[5] > 0.3
    assert envelope[40] == 0.0  # 中间那 2 秒静音
    assert envelope[-5] > 0.3


def test_rms_envelope_missing_file(tmp_path: Path) -> None:
    assert rms_envelope(tmp_path / "nope.wav") == []


def test_rms_envelope_rejects_non_16bit(tmp_path: Path) -> None:
    path = tmp_path / "wide.wav"
    _write_wav(path, [(0.5, 0.5)], width=1)
    assert rms_envelope(path) == []


def test_rms_envelope_feeds_pause_detection(tmp_path: Path) -> None:
    """把"读 WAV"和"找停顿"串起来——这是 production 路径的真实组合。"""
    path = tmp_path / "full.wav"
    _write_wav(path, [(1.0, 0.5), (2.0, 0.0), (1.0, 0.5)])

    envelope = rms_envelope(path, frame_ms=50)
    turn = _turn(text="前面说了一段后面又说了一段", start_ms=0, end_ms=4000)
    pauses = detect_pauses([turn], envelope, frame_ms=50)

    assert len(pauses) == 1
    assert pauses[0].duration_ms == pytest.approx(2000, abs=100)
