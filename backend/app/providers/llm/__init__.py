"""LLM 提供方。新增一家 = 新增一个实现 LLMClient 的模块，不改调用方。"""

from .base import LLMClient, LLMError, LLMMessage, LLMResult, extract_json

__all__ = ["LLMClient", "LLMError", "LLMMessage", "LLMResult", "extract_json"]
