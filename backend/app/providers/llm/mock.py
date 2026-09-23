"""Mock LLM：没有任何 Key 也能跑通全流程。

这不是"占位符"，而是硬要求（PLAN §10）：CI 与无网环境下必须能完整走通
一场面试，否则开发会被外部服务卡住。
"""

from __future__ import annotations

import json

from .base import LLMClient, LLMMessage, LLMResult

# (kind, topic, question)
_MOCK_SCRIPT: list[tuple[str, str, str]] = [
    ("opening", "自我介绍", "先做个自我介绍吧，重点讲一下你最近一段经历，以及你认为自己最核心的能力是什么。"),
    ("new_topic", "项目深挖", "简历里最有挑战的那个项目，你具体负责哪部分？整体方案是怎么设计的？"),
    ("followup", "方案取舍", "你刚提到的这个方案，当时有没有考虑过别的选择？为什么最后选了它？"),
    ("new_topic", "技术细节", "这个项目里最难的技术点是什么？你是怎么定位问题并解决的？"),
    ("followup", "结果量化", "你说的效果提升，能具体量化到什么程度？用什么指标衡量的？"),
    ("new_topic", "行为面试", "讲一次你和同事意见出现冲突的经历，最后是怎么解决的？"),
    ("new_topic", "技术深度", "如果访问量突然涨十倍，你那套设计最先撑不住的是哪一环？你会怎么改？"),
    ("new_topic", "职业规划", "未来两年你希望自己在哪些方面有明显成长？为什么？"),
    ("closing", "收尾", "我的问题先问到这里。你有什么想问我的吗？"),
]


class MockLLMClient(LLMClient):  # type: ignore[misc]
    name = "mock"

    async def complete(
        self,
        *,
        task: str,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        model: str | None = None,
        json_object: bool = False,
    ) -> LLMResult:
        if task == "interview_turn":
            text = self._next_question(messages)
        else:
            text = json.dumps(
                {
                    "mock": True,
                    "task": task,
                    "summary": "（Mock 模式：这里会返回真实模型的分析结果）",
                },
                ensure_ascii=False,
            )

        prompt_chars = sum(len(m.content) for m in messages)
        return LLMResult(
            text=text,
            task=task,
            model=model or f"mock:{task}",
            provider=self.name,
            prompt_tokens=prompt_chars // 2,
            completion_tokens=len(text) // 2,
            latency_ms=5,
        )

    @staticmethod
    def _next_question(messages: list[LLMMessage]) -> str:
        """用已有 assistant 轮次数量定位脚本位置，保证多轮对话可复现。"""
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if "收尾" in last_user:
            kind, topic, question = _MOCK_SCRIPT[-1]
        else:
            asked = sum(1 for m in messages if m.role == "assistant")
            kind, topic, question = _MOCK_SCRIPT[min(asked, len(_MOCK_SCRIPT) - 1)]
        return json.dumps({"kind": kind, "topic": topic, "question": question}, ensure_ascii=False)
