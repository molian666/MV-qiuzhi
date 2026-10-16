"""LLM 空内容重试差异化 + 出题失败题库兜底。

背景（2026-10-16 会话 28 实况）：答完题后 DeepSeek 连续 3 次返回空 content
（原样重试无效），面试卡在"生成问题失败：deepseek 返回了空内容"。
本轮两个修复都要有测试钉住：
1. `Interviewer.next_question` 重试**不是原样重发**：第二次起压低温度并追加
   "直接输出"指令（空 content 常因模型把 token 全花在思考上）；
2. `VoiceSessionRuntime._submit_answer` 出题失败时从题库兜底，面试不卡死。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.models.core import QuestionBank
from app.providers.llm.base import LLMClient, LLMError, LLMResult
from app.providers.speech.mock import MockSpeechProvider
from app.realtime.audio_store import AudioStore
from app.realtime.interviewer import Interviewer
from app.realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime

from .audio_fixtures import silence, tone


class _RetryThenSuccessLLM(LLMClient):  # type: ignore[misc]
    """第一次抛空内容、第二次成功；记录每次调用的 temperature 与消息数。"""

    name = "retry-fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        task: str,
        messages: list[Any],
        temperature: float = 0.7,
        model: str | None = None,
        json_object: bool = False,
    ) -> LLMResult:
        self.calls.append({"temperature": temperature, "message_count": len(messages), "last": messages[-1].content})
        if len(self.calls) == 1:
            raise LLMError(
                "deepseek 返回了空内容（choices[0].message.content 为空，模型可能把全部输出花在了思考上）"
            )
        return LLMResult(
            text=json.dumps(
                {"kind": "new_topic", "topic": "重试话题", "question": "第二次尝试才给出的问题"},
                ensure_ascii=False,
            ),
            task=task,
            model="fake",
            provider="fake",
            prompt_tokens=10,
            completion_tokens=10,
            latency_ms=1,
        )


async def test_retry_changes_temperature_and_adds_directive() -> None:
    """重试不是原样重发：第二次尝试要压温度 + 追加直接输出指令。"""
    llm = _RetryThenSuccessLLM()
    interviewer = Interviewer(llm=llm)

    plan, _ = await interviewer.next_question(turns=[], target=None, resume=None, max_questions=2)

    assert len(llm.calls) == 2
    first, second = llm.calls
    assert first["temperature"] == 0.85
    assert second["temperature"] == 0.3, "重试必须压低温度，逼模型跳过思考直接给题"
    assert second["message_count"] == first["message_count"] + 1
    assert "直接输出" in second["last"]
    assert plan.question == "第二次尝试才给出的问题"


class _AlwaysEmptyLLM(LLMClient):  # type: ignore[misc]
    """每次都返回空内容：逼出题路径走到题库兜底。"""

    name = "always-empty-fake"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        *,
        task: str,
        messages: list[Any],
        temperature: float = 0.7,
        model: str | None = None,
        json_object: bool = False,
    ) -> LLMResult:
        self.calls += 1
        raise LLMError(
            "deepseek 返回了空内容（choices[0].message.content 为空，模型可能把全部输出花在了思考上）"
        )


async def _make_failing_runtime(
    client: TestClient,
    tmp_path: Path,
    llm: LLMClient,
) -> tuple[VoiceSessionRuntime, list[dict[str, Any]], int]:
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": 2}).json()
    session_id = int(created["id"])
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    store = AudioStore(tmp_path / "audio", session_id, sample_rate=16000)
    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=llm,
        speech=MockSpeechProvider(),
        store=store,
        sink=sink,
        config=VoiceRuntimeConfig(
            max_questions=2,
            text_fallback=True,
            silence_ms=300,
            min_answer_ms=200,
        ),
    )
    await runtime.start()
    return runtime, events, session_id


async def test_question_fallback_from_bank_when_llm_fails(
    client: TestClient, tmp_path: Path
) -> None:
    """模型连续失败时从题库抽题顶上：面试不卡死，也不发 error 事件。"""
    with session_scope() as db:
        db.add(
            QuestionBank(
                text="讲讲你做过的最复杂的机器视觉项目，以及你在里面负责的部分。",
                tags=["机器视觉"],
                question_type="open",
                type="general",
            )
        )
        db.commit()

    llm = _AlwaysEmptyLLM()
    runtime, events, _ = await _make_failing_runtime(client, tmp_path, llm)

    await runtime.handle({"type": "text", "text": "第一轮回答"})
    await runtime.close()

    kinds = [event["type"] for event in events]
    assert "error" not in kinds, "有题库兜底时不该报错卡死面试"
    texts = [event.get("text", "") for event in events if event["type"] == "interviewer_text"]
    assert any("机器视觉项目" in text for text in texts), "兜底题应来自题库"


async def test_question_fallback_preserves_closing_phase(
    client: TestClient, tmp_path: Path
) -> None:
    """最后一题（收尾题）生成失败也要兜底：题发出去、进入收尾、等候选人提问。"""
    with session_scope() as db:
        db.add(
            QuestionBank(
                text="我的问题先到这里。你有什么想问我的吗？",
                tags=[],
                question_type="open",
                type="general",
            )
        )
        db.commit()

    llm = _AlwaysEmptyLLM()
    runtime, events, _ = await _make_failing_runtime(client, tmp_path, llm)

    # max_questions=2：第一次回答后出题失败已兜底，第二次回答后再失败→收尾题兜底
    await runtime.handle({"type": "text", "text": "第一轮回答"})
    await runtime.handle({"type": "text", "text": "第二轮回答"})

    kinds = [event["type"] for event in events]
    assert "error" not in kinds
    assert "awaiting_question" in kinds, "收尾题兜底后应进入候选人提问阶段"
    assert "finished" not in kinds, "收尾不是结束"


async def test_error_event_when_bank_is_empty(
    client: TestClient, tmp_path: Path
) -> None:
    """题库为空时保持原行为：发 error 事件，而不是伪造一道题。"""
    llm = _AlwaysEmptyLLM()
    runtime, events, _ = await _make_failing_runtime(client, tmp_path, llm)

    await runtime.handle({"type": "text", "text": "第一轮回答"})
    await runtime.close()

    kinds = [event["type"] for event in events]
    assert "error" in kinds
    assert any("生成问题失败" in event.get("message", "") for event in events if event["type"] == "error")
