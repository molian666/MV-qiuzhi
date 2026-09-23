"""QwenRealtimeSpeechProvider 的协议映射测试。

用注入的假连接驱动，**不需要真实 Key**：验证"我们发出的消息"与"我们如何翻译上游事件"。
真机接入时最可能出问题的三处（session.update 字段、delta 拼接、cancel response）都在这里锚定了预期。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import pytest

from app.providers.speech.base import SpeechError, SpeechEvent
from app.providers.speech.qwen import QwenRealtimeSpeechProvider


class FakeSocket:
    """最小可用的假 WS：脚本化吐出上游事件，并记录我们发出去的消息。"""

    def __init__(self, incoming: list[str]) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> str:
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


def _provider(socket: FakeSocket) -> QwenRealtimeSpeechProvider:
    async def connect(_url: str, _headers: dict[str, str]) -> FakeSocket:
        return socket

    return QwenRealtimeSpeechProvider(api_key="test-key", connect_factory=connect)


def _recorder(events: list[SpeechEvent]) -> Callable[[SpeechEvent], Awaitable[None]]:
    async def handler(event: SpeechEvent) -> None:
        events.append(event)

    return handler


async def _drain(provider: QwenRealtimeSpeechProvider) -> None:
    reader = provider._reader  # noqa: SLF001 - 测试内直接等内部读取任务收尾
    assert reader is not None
    await asyncio.wait_for(reader, timeout=2.0)


async def test_handshake_disables_upstream_vad() -> None:
    socket = FakeSocket([])
    provider = _provider(socket)
    await provider.start(on_event=_recorder([]))

    assert socket.sent[0]["type"] == "session.update"
    session = socket.sent[0]["session"]
    # 关键：上游 VAD 必须关闭，回合边界交给本地 TurnController
    assert session["turn_detection"] is None
    assert session["input_audio_format"] == "pcm16"
    assert session["modalities"] == ["text"]

    await provider.close()


async def test_maps_transcription_and_speech_events() -> None:
    socket = FakeSocket(
        [
            json.dumps({"type": "input_audio_buffer.speech_started"}),
            json.dumps({"type": "conversation.item.input_audio_transcription.delta", "text": "你好"}),
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "你好，我负责订单系统",
                }
            ),
            json.dumps({"type": "input_audio_buffer.speech_stopped"}),
        ]
    )
    provider = _provider(socket)
    events: list[SpeechEvent] = []
    await provider.start(on_event=_recorder(events))
    await _drain(provider)
    await provider.close()

    assert [event.kind for event in events] == ["speech_started", "partial", "final", "speech_stopped"]
    assert events[1].text == "你好"
    assert events[2].text == "你好，我负责订单系统"


async def test_audio_append_and_commit_payloads() -> None:
    socket = FakeSocket([])
    provider = _provider(socket)
    await provider.start(on_event=_recorder([]))

    await provider.append_audio(b"\x01\x02\x03")
    await provider.commit()
    await provider.close()

    assert socket.sent[1] == {"type": "input_audio_buffer.append", "audio": "AQID"}
    assert socket.sent[2] == {"type": "input_audio_buffer.commit"}


async def test_response_created_is_cancelled() -> None:
    """我们只要转写：它自己生成的回复要立刻取消，别白烧 token。"""
    socket = FakeSocket(
        [
            json.dumps({"type": "response.created"}),
            json.dumps({"type": "error", "error": {"message": "boom"}}),
        ]
    )
    provider = _provider(socket)
    events: list[SpeechEvent] = []
    await provider.start(on_event=_recorder(events))
    await _drain(provider)
    await provider.close()

    assert {"type": "response.cancel"} in socket.sent
    assert events[-1].kind == "error"
    assert "boom" in events[-1].message


async def test_missing_api_key_raises_speech_error() -> None:
    provider = QwenRealtimeSpeechProvider(api_key="")
    with pytest.raises(SpeechError, match="DASHSCOPE_API_KEY"):
        await provider.start(on_event=_recorder([]))


async def test_ensure_fresh_reconnects_before_session_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.speech import qwen as qwen_module

    created: list[FakeSocket] = []

    async def connect(_url: str, _headers: dict[str, str]) -> FakeSocket:
        socket = FakeSocket([])
        created.append(socket)
        return socket

    monkeypatch.setattr(qwen_module, "MAX_SESSION_SECONDS", 0)  # 立刻"到期"

    provider = QwenRealtimeSpeechProvider(api_key="k", connect_factory=connect)
    await provider.start(on_event=_recorder([]))
    await provider.ensure_fresh()
    await provider.close()

    assert len(created) == 2  # 首次连接 + 重连
    assert created[0].closed is True  # 旧连接被关掉
    assert provider.name == "qwen-realtime"


async def test_requires_text_fallback_flag() -> None:
    assert QwenRealtimeSpeechProvider(api_key="k").requires_text_fallback is False
