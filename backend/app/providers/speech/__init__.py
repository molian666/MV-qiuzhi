"""语音（ASR）提供方。新增一家 = 新增一个实现 SpeechProvider 的模块。"""

from .base import SpeechError, SpeechEvent, SpeechEventHandler, SpeechProvider
from .mock import MockSpeechProvider
from .qwen import QwenRealtimeSpeechProvider

__all__ = [
    "MockSpeechProvider",
    "QwenRealtimeSpeechProvider",
    "SpeechError",
    "SpeechEvent",
    "SpeechEventHandler",
    "SpeechProvider",
]
