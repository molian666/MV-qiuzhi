"""Mock 语音提供方：没有 ASR Key 时让整条链路照常运转。

它**不是占位符**，而是一种明确的降级模式：
- 音频依然被采集、落盘（full.wav / turn_*.wav），所以录音与回放链路是真的；
- 只是"音频→文本"这一步由用户手输的文字替代（`requires_text_fallback = True`），
  上层据此提示用户"当前是文字替身模式"，而不是假装已经识别了语音。

有 Key 时换成 `QwenRealtimeSpeechProvider`，上层代码一行不用改。
"""

from __future__ import annotations

from typing import Any

from .base import SpeechEventHandler


class MockSpeechProvider:
    name = "mock"
    sample_rate = 16000
    requires_text_fallback = True

    def __init__(self) -> None:
        self._on_event: SpeechEventHandler | None = None
        self.bytes_received = 0
        self.started = False

    async def start(self, *, on_event: SpeechEventHandler, silence_ms: int = 800) -> None:
        self._on_event = on_event
        self.started = True

    async def append_audio(self, pcm: bytes) -> None:
        # 音频由调用方交给 AudioStore 落盘；这里只需要吞掉，保持行为一致
        self.bytes_received += len(pcm)

    async def commit(self) -> None:
        """无可转写内容：等待上层用 `text` 事件提供文字替身。"""

    async def close(self) -> None:
        self.started = False
        self._on_event = None

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bytes_received": self.bytes_received,
            "requires_text_fallback": self.requires_text_fallback,
        }
