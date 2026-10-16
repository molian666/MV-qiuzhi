"""面试官回复生成失败时的行为（修复「正在思考但什么都不输出」）：

- 空返回体不再静默落库：Interviewer 自动重试，重试成功则正常继续；
- 连续失败返回 502，且**绝不落空回合**（否则界面就是"思考完只有空白气泡"）；
- `POST /api/sessions/{id}/retry-question` 只重新生成面试官回复，
  不重复落候选人回答（复用 /answer 会把对话与回答计数弄脏）；
- OpenAI 兼容客户端把空 content 视为失败而非成功。
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from app import deps
from app.providers.llm.base import LLMError, LLMMessage, LLMResult
from app.providers.llm.openai_compat import OpenAICompatClient
from app.realtime.interviewer import parse_plan


def _json(question: str, kind: str = "new_topic", topic: str = "测试") -> LLMResult:
    return LLMResult(
        text=f'{{"kind":"{kind}","topic":"{topic}","question":"{question}"}}',
        task="interview_turn",
        model="scripted",
        provider="scripted",
    )


def _empty(task: str) -> LLMResult:
    """模拟模型"把全部输出花在思考上"、content 为空。"""
    return LLMResult(text="", task=task, model="scripted", provider="scripted")


class ScriptedLLM:
    """按调用次数出脚本：每次调用都问 handler 要结果。

    脚本外再被调用就抛 AssertionError——让"多调了一次"在测试里立刻暴露，
    而不是静默复用最后一个结果把重试次数掩盖掉。
    """

    name = "scripted"

    def __init__(self, handler: Callable[[str, int], LLMResult]) -> None:
        self._handler = handler
        self.calls = 0

    async def complete(
        self,
        *,
        task: str,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        model: str | None = None,
        json_object: bool = False,
    ) -> LLMResult:
        self.calls += 1
        return self._handler(task, self.calls)


# --------------------------------------------------------------------------- #
# Interviewer 自动重试
# --------------------------------------------------------------------------- #
def test_empty_response_is_retried_automatically(client, monkeypatch) -> None:
    """第一轮回答返回空 → 自动重试 → 正常返回问题，全程只落一条面试官回合。"""

    def handler(task: str, call: int) -> LLMResult:
        if call == 1:  # 创建会话的开场问题
            return _json("请先做自我介绍。", kind="opening")
        if call == 2:  # 第一轮回答：模型返回空 → 触发自动重试
            return _empty(task)
        if call == 3:  # 重试成功
            return _json("介绍一下你的订单项目？", topic="项目")
        raise AssertionError(f"意外调用 #{call}: {task}")

    fake = ScriptedLLM(handler)
    monkeypatch.setattr(deps, "build_llm_client", lambda: fake)
    deps.reset_llm_client()

    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    body = client.post(f"/api/sessions/{session_id}/answer", json={"text": "我的第一轮回答"}).json()

    assert body["question"]["text"] == "介绍一下你的订单项目？"
    assert [t["speaker"] for t in body["session"]["turns"]] == ["interviewer", "candidate", "interviewer"]
    assert all(t["text"].strip() for t in body["session"]["turns"])


def test_consistently_empty_returns_502_and_never_persists_empty_turn(client, monkeypatch) -> None:
    """连续空内容：502，绝不落空回合，会话保持 live 可继续。"""

    def handler(task: str, call: int) -> LLMResult:
        if call == 1:
            return _json("请先做自我介绍。", kind="opening")
        return _empty(task)

    fake = ScriptedLLM(handler)
    monkeypatch.setattr(deps, "build_llm_client", lambda: fake)
    deps.reset_llm_client()

    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    response = client.post(f"/api/sessions/{session_id}/answer", json={"text": "答一轮"})

    assert response.status_code == 502
    detail = client.get(f"/api/sessions/{session_id}").json()
    assert detail["status"] == "live"  # 会话没被误杀，还能继续
    assert [t["speaker"] for t in detail["turns"]] == ["interviewer", "candidate"]
    assert all(t["text"].strip() for t in detail["turns"])


# --------------------------------------------------------------------------- #
# retry-question 端点
# --------------------------------------------------------------------------- #
def test_retry_question_regenerates_only_interviewer_reply(client, monkeypatch) -> None:
    """回答生成失败后：retry-question 只补面试官回复，不重复落候选人回答。"""

    def handler(task: str, call: int) -> LLMResult:
        if call == 1:
            return _json("请先做自我介绍。", kind="opening")
        if call in (2, 3, 4):  # 第一轮回答：连续 3 次空 → 该轮 502
            return _empty(task)
        if call == 5:  # 重试生成回复：一次成功
            return _json("重试后的问题？", topic="项目")
        raise AssertionError(f"意外调用 #{call}: {task}")

    fake = ScriptedLLM(handler)
    monkeypatch.setattr(deps, "build_llm_client", lambda: fake)
    deps.reset_llm_client()

    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    failed = client.post(f"/api/sessions/{session_id}/answer", json={"text": "答一轮"})
    assert failed.status_code == 502

    before = client.get(f"/api/sessions/{session_id}").json()
    assert [t["speaker"] for t in before["turns"]] == ["interviewer", "candidate"]
    assert before["answer_count"] == 1

    body = client.post(f"/api/sessions/{session_id}/retry-question").json()
    assert body["question"]["text"] == "重试后的问题？"
    after = body["session"]
    assert [t["speaker"] for t in after["turns"]] == ["interviewer", "candidate", "interviewer"]
    assert after["answer_count"] == 1  # 重试没有重复落候选人回答
    assert after["turns"][-1]["text"] == "重试后的问题？"


def test_retry_question_404_and_409_guard(client) -> None:
    assert client.post("/api/sessions/999/retry-question").status_code == 404

    session_id = client.post("/api/sessions", json={"max_questions": 2}).json()["id"]
    # 正常进行中、最后一个是面试官回合 → 没有欠着的回复，无需重试
    assert client.post(f"/api/sessions/{session_id}/retry-question").status_code == 409
    # 会话结束后 → 409
    assert client.post(f"/api/sessions/{session_id}/finish").status_code == 200
    assert client.post(f"/api/sessions/{session_id}/retry-question").status_code == 409


# --------------------------------------------------------------------------- #
# 单元级：空内容必须按失败处理
# --------------------------------------------------------------------------- #
def test_parse_plan_rejects_empty_text() -> None:
    with pytest.raises(LLMError) as excinfo:
        parse_plan(LLMResult(text="", task="interview_turn", model="m"))
    assert excinfo.value.retryable is True


def test_parse_plan_rejects_empty_json_question() -> None:
    with pytest.raises(LLMError) as excinfo:
        parse_plan(LLMResult(text='{"kind":"new_topic","question":""}', task="interview_turn", model="m"))
    assert excinfo.value.retryable is True


def _compat_client(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAICompatClient:
    return OpenAICompatClient(
        api_key="k",
        base_url="https://api.deepseek.com",
        chat_model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )


async def test_openai_compat_empty_content_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}], "usage": {}})

    client = _compat_client(handler)
    with pytest.raises(LLMError) as excinfo:
        await client.complete(task="interview_turn", messages=[LLMMessage("user", "hi")])
    assert excinfo.value.retryable is True


async def test_openai_compat_null_content_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}], "usage": {}})

    client = _compat_client(handler)
    with pytest.raises(LLMError):
        await client.complete(task="interview_turn", messages=[LLMMessage("user", "hi")])


async def test_openai_compat_4xx_is_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    client = _compat_client(handler)
    with pytest.raises(LLMError) as excinfo:
        await client.complete(task="interview_turn", messages=[LLMMessage("user", "hi")])
    assert excinfo.value.retryable is False
