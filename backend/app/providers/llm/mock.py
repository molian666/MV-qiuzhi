"""Mock LLM：没有任何 Key 也能跑通全流程。

这不是"占位符"，而是硬要求（PLAN §10）：CI 与无网环境下必须能完整走通
一场面试，否则开发会被外部服务卡住。
"""

from __future__ import annotations

import json

from .base import LLMClient, LLMMessage, LLMResult
from .mock_answers import build_mock_questions, build_mock_self_intro, build_mock_sensitive
from .mock_design import build_mock_design_review
from .mock_generate import build_mock_generated_questions
from .mock_negotiation import build_mock_negotiation
from .mock_pacing import build_mock_pacing_labels
from .mock_say_hi import build_mock_say_hi
from .mock_scoring import build_mock_scoring_json
from .mock_story import build_mock_stories
from .mock_training import build_mock_training_grade, build_mock_training_questions

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
        elif task == "scoring":
            text = build_mock_scoring_json(messages)
        elif task == "training_question":
            text = build_mock_training_questions(messages)
        elif task == "training_grade":
            text = build_mock_training_grade(messages)
        elif task == "pacing_label":
            text = build_mock_pacing_labels(messages)
        elif task == "say_hi":
            # 招呼语是纯文本，不是 JSON
            text = build_mock_say_hi(messages)
        elif task == "story_card":
            text = build_mock_stories(messages)
        elif task == "self_intro":
            text = build_mock_self_intro(messages)
        elif task == "sensitive_answer":
            text = build_mock_sensitive(messages)
        elif task == "questions_to_ask":
            text = build_mock_questions(messages)
        elif task == "negotiation":
            text = build_mock_negotiation(messages)
        elif task == "algo_review":
            text = build_mock_algo_review(messages)
        elif task == "design_review":
            text = build_mock_design_review(messages)
        elif task == "vision_question_generate":
            text = build_mock_generated_questions(messages)
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
