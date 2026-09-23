"""providers/llm 单测：JSON 抽取容错 + Mock 脚本可复现（无 Key 也能跑）。"""

from __future__ import annotations

import pytest

from app.providers.llm.base import LLMMessage, extract_json
from app.providers.llm.mock import MockLLMClient


@pytest.mark.parametrize(
    "text",
    [
        '{"kind": "opening", "question": "你好"}',
        '```json\n{"kind": "opening", "question": "你好"}\n```',
        '好的，这是我的输出：\n{"kind": "opening", "question": "你好"}\n以上。',
        '```\n{"kind": "opening", "question": "你好"}\n```',
    ],
)
def test_extract_json_tolerates_wrappers(text: str) -> None:
    assert extract_json(text)["kind"] == "opening"


def test_extract_json_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        extract_json("这完全不是 JSON")


async def test_mock_starts_with_opening() -> None:
    client = MockLLMClient()
    result = await client.complete(
        task="interview_turn",
        messages=[LLMMessage("system", "system prompt"), LLMMessage("user", "（现在开始面试）")],
    )
    payload = extract_json(result.text)
    assert payload["kind"] == "opening"
    assert payload["question"]
    assert result.prompt_tokens > 0


async def test_mock_walks_the_script_as_turns_grow() -> None:
    """脚本顺序：opening → new_topic(项目深挖) → followup(方案取舍) → …"""
    client = MockLLMClient()
    after_opening = [
        LLMMessage("system", "system prompt"),
        LLMMessage("assistant", "先做自我介绍吧"),
        LLMMessage("user", "我是……"),
    ]

    second = extract_json((await client.complete(task="interview_turn", messages=after_opening)).text)
    assert second["kind"] == "new_topic"

    after_second = [
        *after_opening,
        LLMMessage("assistant", second["question"]),
        LLMMessage("user", "我做了……"),
    ]
    third = extract_json((await client.complete(task="interview_turn", messages=after_second)).text)
    assert third["kind"] == "followup"


async def test_mock_recognises_closing_instruction() -> None:
    client = MockLLMClient()
    messages = [
        LLMMessage("system", "system prompt"),
        LLMMessage("assistant", "先做自我介绍吧"),
        LLMMessage("user", "我是……"),
        LLMMessage("user", "（本轮是面试收尾：用一两句话收束整场面试……）"),
    ]
    payload = extract_json((await client.complete(task="interview_turn", messages=messages)).text)
    assert payload["kind"] == "closing"


async def test_mock_scoring_returns_report_shape() -> None:
    """scoring 任务返回完整报告结构（M3 起不再是通用兜底 JSON）。"""
    client = MockLLMClient()
    payload = extract_json((await client.complete(task="scoring", messages=[])).text)
    assert "overall" in payload
    assert isinstance(payload["dimensions"], dict)
    assert payload["dimensions"]
    assert isinstance(payload["items"], list)
    assert payload["summary"]


async def test_mock_unknown_task_falls_back_to_generic_json() -> None:
    """还没实现的任务（M8 的 resume_match 等）也要返回可解析 JSON，别让链路提前炸。"""
    client = MockLLMClient()
    payload = extract_json((await client.complete(task="resume_match", messages=[])).text)
    assert payload["task"] == "resume_match"
