"""TTS 提供方。M1 默认 `client` 模式（浏览器播报），服务端实现待接入。"""

from .base import AudioChunk, TTSNotConfiguredError, TTSProvider, build_tts_provider

__all__ = ["AudioChunk", "TTSNotConfiguredError", "TTSProvider", "build_tts_provider"]
