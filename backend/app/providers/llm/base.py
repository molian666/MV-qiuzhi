"""LLM 提供方抽象。

约定（PLAN §9.1 第 5 条）：**每次调用都必须带 `task` 标签**，
用于按任务换模型、核算成本、对比 prompt 效果。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """LLM 调用失败：网络、鉴权、额度、返回体异常。

    `retryable` 区分「重试可能有用」与「重试必败」：
    网络抖动 / 服务端 5xx / 限流 / 空返回体都值得重试；
    配错 Key、模型不存在这类 4xx 重试只会白等，直接抛给调用方。
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


# 结构化输入的包裹标记：放在协议层，让 Mock / 离线实现也能稳定解析输入，
# 不必去猜 prompt 的自然语言文本（真实模型与 Mock 共用同一份约定）。
RECORD_MARKER_START = "【本场面试记录 JSON】"
RECORD_MARKER_END = "【记录结束】"


@dataclass(slots=True)
class LLMMessage:
    role: str  # system | user | assistant
    content: str

    def to_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class LLMResult:
    text: str
    task: str
    model: str
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class LLMClient(Protocol):
    name: str

    async def complete(
        self,
        *,
        task: str,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        model: str | None = None,
        json_object: bool = False,
    ) -> LLMResult: ...


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里抠出 JSON 对象。

    模型经常会给 ```json 包裹或前后带解释，这里做三步兜底：
    1. 直接解析；2. 解析代码块；3. 截取第一个 `{` 到最后一个 `}`。
    全部失败则抛 ValueError，调用方自行决定降级策略。
    """
    candidates: list[str] = [text.strip()]

    for match in _JSON_BLOCK.finditer(text):
        candidates.append(match.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"无法从模型输出中解析出 JSON：{text[:200]!r}")
