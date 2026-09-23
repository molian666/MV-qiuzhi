"""阿里云百炼实时语音识别（Qwen-Omni-Realtime 的 OpenAI-Realtime 方言）。

协议依据（已在 docs/PLAN.md §6.1.1 核实）：
- 端点：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<model>`
- 头：`Authorization: Bearer $DASHSCOPE_API_KEY`，可选 `OpenAI-Beta: realtime=v1`
- 先发 `session.update` 再发音频；客户端事件 `input_audio_buffer.append` / `commit`
- 服务端事件 `conversation.item.input_audio_transcription.delta|completed`

⚠️ 诚实声明：本实现按官方文档的事件名与字段编写，但**尚未用真实 Key 端到端验证**
（开发机没有 DASHSCOPE_API_KEY）。首次接真机时最可能需要微调的三处已用
`# 待真机校准` 标出：session.update 的字段、delta 事件的拼接方式、是否需要主动 cancel response。

设计取舍：这里用 **manual 模式**（关闭上游 VAD），把"什么时候说完"完全交给本地
`TurnController`——避免上游 VAD 与本地 VAD 打架，也让打断延迟可控。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

from .base import SpeechError, SpeechEvent, SpeechEventHandler

DEFAULT_ENDPOINT = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3-asr-flash-realtime"

# 官方限制：单会话最长 120 分钟，到点前主动重连
MAX_SESSION_SECONDS = 6600  # 110 分钟


class QwenRealtimeSpeechProvider:
    name = "qwen-realtime"
    sample_rate = 16000
    requires_text_fallback = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        trust_env: bool = False,
        # 注入点：测试用假的连接实现替换，避免真的建 socket
        connect_factory: Any = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._trust_env = trust_env
        self._connect_factory = connect_factory

        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._on_event: SpeechEventHandler | None = None
        self._partial = ""
        self._started_at = 0.0
        self._closed = False

    # ------------------------------------------------------------------ #
    async def start(self, *, on_event: SpeechEventHandler, silence_ms: int = 800) -> None:
        if not self._api_key:
            raise SpeechError("未配置 DASHSCOPE_API_KEY：请在 backend/.env 填写，或把语音提供方设为 mock")

        self._on_event = on_event
        await self._connect()
        self._reader = asyncio.create_task(self._read_loop())

    async def _connect(self) -> None:
        url = f"{self._endpoint}?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        try:
            self._ws = await self._connect_ws(url, headers)
        except Exception as exc:  # 网络/鉴权失败都归成 SpeechError，让上层能给出可读提示
            raise SpeechError(f"连接实时语音上游失败：{exc}") from exc

        self._started_at = time.monotonic()
        self._closed = False

        # 待真机校准 ①：session.update 的字段名与取值
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],  # 只要文本，不要它自己生成回复
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm24",
                    "turn_detection": None,  # manual 模式：边界由本地 TurnController 决定
                    "instructions": "你只负责把用户的语音转写成文字，不要生成任何回复内容。",
                },
            }
        )

    async def _connect_ws(self, url: str, headers: dict[str, str]) -> Any:
        if self._connect_factory is not None:
            return await self._connect_factory(url, headers)

        import websockets  # 延迟导入：Mock 模式下不需要这个依赖

        common: dict[str, Any] = {
            "max_size": 16 * 1024 * 1024,
            "ping_interval": 20,
            "open_timeout": 15,
        }
        # 逐级退化探测：websockets 各版本的参数名并不一致，
        # 与其写死版本要求，不如按兼容性从新到旧试。
        # proxy=None 是显式"不要用系统代理"——百炼是国内直连。
        attempts: list[dict[str, Any]] = [
            {"additional_headers": headers, "proxy": None if not self._trust_env else True},
            {"additional_headers": headers},
            {"extra_headers": headers},
        ]

        last_error: Exception | None = None
        for extra in attempts:
            try:
                return await websockets.connect(url, **common, **extra)
            except TypeError as exc:  # 参数不被该版本支持
                last_error = exc
                continue
        raise SpeechError(f"当前 websockets 版本不支持所需的连接参数：{last_error}")

    # ------------------------------------------------------------------ #
    async def ensure_fresh(self) -> None:
        """110 分钟到点前主动重连（上游限制单会话 120 分钟）。"""
        if self._ws is None:
            return
        if time.monotonic() - self._started_at < MAX_SESSION_SECONDS:
            return

        await self._teardown()
        await self._connect()
        self._reader = asyncio.create_task(self._read_loop())

    async def append_audio(self, pcm: bytes) -> None:
        if not pcm or self._ws is None:
            return
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def commit(self) -> None:
        if self._ws is None:
            return
        await self._send({"type": "input_audio_buffer.commit"})

    async def close(self) -> None:
        self._closed = True
        await self._teardown()

    async def _teardown(self) -> None:
        if self._reader is not None:
            reader, self._reader = self._reader, None
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: B014 - CancelledError 是 BaseException 子类
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                pass
            self._ws = None

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise SpeechError("实时语音连接尚未建立")
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            raise SpeechError(f"向上游发送音频失败：{exc}") from exc

    # ------------------------------------------------------------------ #
    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue  # 我们只要文本/事件
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not self._closed:
                await self._emit(SpeechEvent(kind="error", message=f"实时语音连接中断：{exc}"))

    async def _handle(self, data: dict[str, Any]) -> None:
        kind = data.get("type", "")

        if kind == "conversation.item.input_audio_transcription.delta":
            # 待真机校准 ②：官方文档说把 text + stash 拼起来才是当前预览
            preview = (data.get("text") or "") + (data.get("stash") or "")
            if preview:
                self._partial = preview
                await self._emit(SpeechEvent(kind="partial", text=preview))

        elif kind == "conversation.item.input_audio_transcription.completed":
            text = (data.get("transcript") or data.get("text") or self._partial).strip()
            self._partial = ""
            await self._emit(SpeechEvent(kind="final", text=text))

        elif kind == "input_audio_buffer.speech_started":
            await self._emit(SpeechEvent(kind="speech_started"))

        elif kind == "input_audio_buffer.speech_stopped":
            await self._emit(SpeechEvent(kind="speech_stopped"))

        elif kind == "response.created":
            # 待真机校准 ③：我们只要转写，取消它自动生成的回复
            await self._send({"type": "response.cancel"})

        elif kind == "error":
            message = data.get("error", {}).get("message") or json.dumps(data, ensure_ascii=False)[:300]
            await self._emit(SpeechEvent(kind="error", message=message))

    async def _emit(self, event: SpeechEvent) -> None:
        if self._on_event is not None:
            await self._on_event(event)
