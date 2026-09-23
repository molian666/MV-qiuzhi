"""回合控制：这一轮回答什么时候算说完。

PLAN §6.1 要点 1 要求三个因素共同判定，M1 落地其中两个（第三个"是否覆盖关键词"留给 M4 的节奏画像）：
- VAD 静音阈值（`silence_ms`，将由面试官画像的 `wait_ms` 动态调节）
- 最短回答时长（低于它视为噪声/口误，不结束回合）
- 单轮上限（防卡死，强制结束）

设计成纯状态机、自己不带时钟：时间一律由调用方传入，因此可以完全单测。
"""

from __future__ import annotations

from dataclasses import dataclass

from .vad import VadEvent


@dataclass(slots=True)
class TurnControllerConfig:
    min_answer_ms: int = 700  # 短于这个时长的"说话"忽略，多半是咳嗽/噪音
    max_answer_ms: int = 180_000  # 单轮上限，到点强制结束
    wait_after_silence_ms: int = 0  # 静音判定后额外缓冲（给 ASR 出 final 的时间）


@dataclass(slots=True)
class TurnDecision:
    reason: str  # silence | too_long | manual
    at_ms: int
    speech_ms: int


class TurnController:
    def __init__(self, config: TurnControllerConfig | None = None) -> None:
        self.config = config or TurnControllerConfig()
        self._speaking = False
        self._speech_started_ms = 0
        self._ignored_silences = 0
        self._last_now_ms = 0

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def speech_ms(self) -> int:
        return 0 if not self._speaking else self._last_now_ms - self._speech_started_ms

    @property
    def ignored_silences(self) -> int:
        """被当作噪声忽略的"说完"次数——用来诊断麦克风/阈值问题。"""
        return self._ignored_silences

    def on_vad_event(self, event: VadEvent) -> TurnDecision | None:
        self._last_now_ms = event.at_ms

        if event.kind == "speech_started":
            self._speaking = True
            self._speech_started_ms = event.at_ms
            return None

        if event.kind != "speech_stopped":
            return None

        self._speaking = False
        if event.speech_ms < self.config.min_answer_ms:
            # 太短：不当成一次回答，等用户真正说完
            self._ignored_silences += 1
            return None

        return TurnDecision(reason="silence", at_ms=event.at_ms, speech_ms=event.speech_ms)

    def on_tick(self, now_ms: int) -> TurnDecision | None:
        """周期性调用（每个音频分片一次），用于超长回答的强制收口。"""
        self._last_now_ms = now_ms
        if not self._speaking:
            return None
        if now_ms - self._speech_started_ms >= self.config.max_answer_ms:
            self._speaking = False
            return TurnDecision(
                reason="too_long",
                at_ms=now_ms,
                speech_ms=now_ms - self._speech_started_ms,
            )
        return None

    def decide_manual(self, now_ms: int) -> TurnDecision:
        """用户主动结束本轮（点"我答完了"，或语音模式下改用文字提交）。"""
        decision = TurnDecision(
            reason="manual",
            at_ms=now_ms,
            speech_ms=max(0, now_ms - self._speech_started_ms) if self._speaking else 0,
        )
        self._speaking = False
        self._last_now_ms = now_ms
        return decision

    def reset(self) -> None:
        self._speaking = False
        self._ignored_silences = 0
