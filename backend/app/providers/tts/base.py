"""TTS 提供方抽象 + M1 的选型结论。

**M1 结论（重要，别绕路）**：默认走**浏览器端播报**（前端 `speechSynthesis`，`audio_mode=client`）：
- 零额外依赖与费用通道，中文音色开箱可用；
- 让"M1 语音面试"在没有百炼 TTS Key 的情况下也能**真的听得见**面试官说话，验收才算数；
- 服务端 TTS 只保留协议位（本文件的 `TTSProvider`）。将来选定厂商后实现它，
  把 `audio_mode` 切到 `server`，音频通过 `audio_chunk` 事件下发——前端播放器不用改。

之所以不在这里先写一个"看起来能用但没验证过"的厂商实现：
用错的接口去写代码，比先留接口更贵——后者只是等待，前者会让人误以为链路已通。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class AudioChunk:
    pcm: bytes
    sample_rate: int


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    sample_rate: int

    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]: ...

    async def close(self) -> None: ...


class TTSNotConfiguredError(RuntimeError):
    """未配置服务端 TTS：上层应回退到浏览器端播报（audio_mode=client）。"""


def build_tts_provider() -> TTSProvider | None:
    """M1 固定返回 None（= 浏览器端播报）。

    接入步骤（等选定厂商与音色后）：
    1. 在本包新增 `qwen_tts.py` 等实现 `TTSProvider`；
    2. 在这里按 `settings.tts_provider` 返回对应实例；
    3. `session_runtime` 会自动把 `audio_mode` 从 `client` 切成 `server`。
    """
    return None
