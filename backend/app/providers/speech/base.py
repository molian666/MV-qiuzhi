"""语音（ASR）提供方抽象。

职责边界：本层只做「音频 → 文本」，不管"什么时候该接话"（那是 TurnController 的事），
也不管"回答内容好不好"（那是 scoring 的事）。因此换供应商不影响产品逻辑。

约定：provider 通过 `on_event` 回调把事件推给上层，事件类型见 `SpeechEvent.kind`：
- `speech_started` / `speech_stopped`：上游自带 VAD 时的切分（本地 VAD 也会发，二者可共存）
- `partial`：中间转写（用于实时字幕）
- `final`：一段音频的最终转写
- `error`：上游异常
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class SpeechEvent:
    kind: str  # speech_started | speech_stopped | partial | final | error
    text: str = ""
    at_ms: int = 0
    message: str = ""


SpeechEventHandler = Callable[[SpeechEvent], Awaitable[None]]


class SpeechError(RuntimeError):
    """语音上游失败（网络、鉴权、协议异常）。"""


@runtime_checkable
class SpeechProvider(Protocol):
    name: str
    sample_rate: int
    # True 表示"只有真实音频才能出文本"；False 表示该 provider 需要外部提供文字
    # （Mock 模式：音频照常采集与存档，但转写文本由用户输入替代）
    requires_text_fallback: bool

    async def start(self, *, on_event: SpeechEventHandler, silence_ms: int = 800) -> None: ...

    async def append_audio(self, pcm: bytes) -> None: ...

    async def commit(self) -> None: ...

    async def close(self) -> None: ...
