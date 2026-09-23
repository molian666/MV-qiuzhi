"""FastAPI 依赖：LLM 客户端与数据会话。"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterator

from sqlmodel import Session

from .config import get_settings
from .db import get_session as _get_session
from .providers.llm.base import LLMClient
from .providers.llm.deepseek import DeepSeekClient
from .providers.llm.mock import MockLLMClient
from .providers.speech.base import SpeechProvider
from .providers.speech.mock import MockSpeechProvider
from .providers.speech.qwen import QwenRealtimeSpeechProvider


def build_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "deepseek":
        return DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            chat_model=settings.deepseek_chat_model,
            reasoner_model=settings.deepseek_reasoner_model,
            timeout=settings.llm_timeout_seconds,
            trust_env=settings.trust_env_proxy,
        )
    return MockLLMClient()


@lru_cache
def _client_singleton() -> LLMClient:
    return build_llm_client()


def get_llm() -> LLMClient:
    return _client_singleton()


def reset_llm_client() -> None:
    """测试用：让下次取用时按最新配置重建。"""
    _client_singleton.cache_clear()


def build_speech_provider() -> SpeechProvider:
    """语音提供方**每次返回新实例**：它是有状态的（长连接、转写缓冲），不能共享。

    共享实例会让两场面试互相串台——这类 bug 很隐蔽，所以在工厂层就杜绝。
    """
    settings = get_settings()
    if settings.speech_provider == "qwen":
        return QwenRealtimeSpeechProvider(
            api_key=settings.dashscope_api_key,
            model=settings.dashscope_asr_model,
            endpoint=settings.dashscope_realtime_endpoint,
            trust_env=settings.trust_env_proxy,
        )
    return MockSpeechProvider()


def get_speech_provider() -> SpeechProvider:
    return build_speech_provider()


def get_session() -> Iterator[Session]:
    yield from _get_session()
