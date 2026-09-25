"""QwenRealtimeSpeechProvider 的协议映射测试。

用注入的假连接驱动，**不需要真实 Key**：验证"我们发出的消息"与"我们如何翻译上游事件"。

⚠️ 这个文件曾经把 `input_audio_format` 断言成 `pcm16`（Omni 系列的取值），
结果测试全绿而真机报 `1011 Audio format is not valid 'pcm16'!`。
**锚错预期比没有测试更糟**——它给的是虚假的安全感。
断言协议字段时以官方客户端事件文档为准，不要照另一套相似协议的示例推。
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
    # 取值见 test_session_update_uses_asr_series_audio_format：
    # 这里曾经写死 `pcm16`，把错的值锚了住，真机才炸出来
    assert session["input_audio_format"] == "pcm"

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


async def test_upstream_error_gets_actionable_hint() -> None:
    """真机收到的是 `1011 ... Audio format is not valid 'pcm16'!` 这种文本。

    对使用者完全不透明，而语音链路的排查本来就难（连接是通的、报错在握手之后，
    看起来像"连上了又断"）。所以要在原始错误后面补一句"下一步查什么"。
    """
    socket = FakeSocket(
        [
            json.dumps(
                {
                    "type": "error",
                    "error": {"message": "Audio format is not valid 'pcm16'!"},
                }
            )
        ]
    )
    events: list[SpeechEvent] = []
    provider = _provider(socket)

    await provider.start(on_event=_recorder(events))
    await _drain(provider)
    await provider.close()

    message = events[-1].message
    assert events[-1].kind == "error"
    assert "Audio format is not valid" in message, "原始错误要留着，方便对照文档"
    assert "pcm" in message, "要补一句可操作的说明"


async def test_unknown_upstream_error_has_no_invented_hint() -> None:
    """认不出的错误不要硬凑建议——指错方向比不给更浪费时间。"""
    socket = FakeSocket(
        [json.dumps({"type": "error", "error": {"message": "some brand new failure"}})]
    )
    events: list[SpeechEvent] = []
    provider = _provider(socket)

    await provider.start(on_event=_recorder(events))
    await _drain(provider)
    await provider.close()

    assert events[-1].message == "some brand new failure"


async def test_missing_api_key_raises_speech_error() -> None:
    provider = QwenRealtimeSpeechProvider(api_key="")
    with pytest.raises(SpeechError, match="DASHSCOPE_API_KEY"):
        await provider.start(on_event=_recorder([]))


# --------------------------------------------------------------------------- #
# session.update 的取值 —— 真机踩过的坑
# --------------------------------------------------------------------------- #
async def test_session_update_uses_asr_series_audio_format() -> None:
    """★ `input_audio_format` 必须是 **ASR 系列**的取值 `pcm`。

    这条是**真机踩出来的**：写成 Omni 系列的 `pcm16` 会收到
    `1011 Internal service error: Audio format is not valid 'pcm16'!`，
    而且是在连接建立**之后**才报——看起来像"连上了又断"，很难第一眼归因到格式。

    两套协议长得像但取值不通用：ASR 是 `pcm` / `opus`，Omni 是 `pcm16` / `pcm24`。
    """
    socket = FakeSocket([])
    provider = _provider(socket)

    await provider.start(on_event=_recorder([]), silence_ms=800)
    await provider.close()

    update = socket.sent[0]
    assert update["type"] == "session.update"
    session = update["session"]

    assert session["input_audio_format"] == "pcm"
    assert session["sample_rate"] == 16000
    assert session["turn_detection"] is None, "null 才是 manual 模式，有值等于开了上游 VAD"

    # Omni 系列才有的字段：ASR 没有输出音频，传了只会招错
    assert "output_audio_format" not in session
    assert "modalities" not in session


async def test_session_update_feeds_glossary_as_context() -> None:
    """名词表要进 `input_audio_transcription.corpus.text`（官方的上下文偏置）。

    这是识别阶段的改善——比事后用同音字表纠错更上游，两者叠加。
    """
    socket = FakeSocket([])
    provider = _provider(socket)

    await provider.start(on_event=_recorder([]), silence_ms=800)
    await provider.close()

    transcription = socket.sent[0]["session"]["input_audio_transcription"]
    assert transcription["language"] == "zh", "中文面试要显式指定语言"

    corpus = transcription["corpus"]["text"]
    for term in ("远心镜头", "亚像素", "九点标定", "Halcon"):
        assert term in corpus, f"名词表里缺 {term}"


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
