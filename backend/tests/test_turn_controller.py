"""回合控制测试：什么时候算"这一轮回答结束"。"""

from __future__ import annotations

from app.realtime.turn_controller import TurnController, TurnControllerConfig
from app.realtime.vad import VadEvent


def test_short_speech_is_ignored() -> None:
    controller = TurnController(TurnControllerConfig(min_answer_ms=700))
    assert controller.on_vad_event(VadEvent("speech_started", 0, 0.3)) is None
    assert controller.on_vad_event(VadEvent("speech_stopped", 300, 0.01, speech_ms=300)) is None
    assert controller.ignored_silences == 1
    assert controller.is_speaking is False


def test_real_answer_ends_turn() -> None:
    controller = TurnController(TurnControllerConfig(min_answer_ms=700))
    controller.on_vad_event(VadEvent("speech_started", 0, 0.3))
    decision = controller.on_vad_event(VadEvent("speech_stopped", 2100, 0.01, speech_ms=2100))

    assert decision is not None
    assert decision.reason == "silence"
    assert decision.speech_ms == 2100
    assert controller.ignored_silences == 0


def test_too_long_forces_end() -> None:
    controller = TurnController(TurnControllerConfig(max_answer_ms=1000))
    controller.on_vad_event(VadEvent("speech_started", 0, 0.3))

    assert controller.on_tick(500) is None
    decision = controller.on_tick(1200)
    assert decision is not None
    assert decision.reason == "too_long"
    assert decision.speech_ms == 1200
    # 强制收口后不再重复触发
    assert controller.on_tick(2000) is None


def test_manual_end_reports_speech_duration() -> None:
    controller = TurnController()
    controller.on_vad_event(VadEvent("speech_started", 100, 0.3))

    decision = controller.decide_manual(1500)
    assert decision.reason == "manual"
    assert decision.speech_ms == 1400
    assert controller.is_speaking is False


def test_tick_without_speech_is_noop() -> None:
    controller = TurnController()
    assert controller.on_tick(999_999) is None


def test_reset_clears_speaking_state() -> None:
    controller = TurnController()
    controller.on_vad_event(VadEvent("speech_started", 0, 0.3))
    controller.reset()
    assert controller.is_speaking is False
    assert controller.ignored_silences == 0
