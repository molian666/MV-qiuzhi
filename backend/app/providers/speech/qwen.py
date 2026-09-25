"""阿里云百炼实时语音识别（Qwen-ASR-Realtime 的 OpenAI-Realtime 方言）。

协议依据（已按官方客户端事件文档核实）：
- 端点：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<model>`
- 头：`Authorization: Bearer $DASHSCOPE_API_KEY`，`OpenAI-Beta: realtime=v1`
- 先发 `session.update` 再发音频；客户端事件 `input_audio_buffer.append` / `commit`
- 服务端事件 `conversation.item.input_audio_transcription.delta|completed`

⚠️ **字段取值是踩过坑的**，改动前先读这段：

`input_audio_format` 在 **ASR 系列**只接受 **`pcm` / `opus`**（默认 `pcm`）；
而 `pcm16` / `pcm24` 是 **Omni 系列**的取值。两者协议相似但取值不同，
混用会直接收到 `1011 Internal service error: Audio format is not valid 'pcm16'!`
——这个错误实际发生过。改字段时先查文档，别照 Omni 的示例推。

设计取舍：这里用 **manual 模式**（`turn_detection: null`），把"什么时候说完"完全交给本地
`TurnController`——避免上游 VAD 与本地 VAD 打架，也让打断延迟可控。

另外把机器视觉名词表塞进 `input_audio_transcription.corpus.text`（官方叫"上下文偏置"）：
术语音在通用语料里罕见，ASR 会把「亚像素」听成「亚相素」。给一份术语清单当背景，
识别阶段就能少错一些——这比事后纠错更上游，两者是叠加的。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

from ...analysis.glossary import glossary_for_prompt
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

        # Qwen-ASR-Realtime 的 session 配置。
        #
        # 三个取值都不能想当然：
        # - input_audio_format 只接受 pcm / opus（pcm16 是 Omni 的取值，会 1011）
        # - output_audio_format 是 Omni 的字段，ASR 没有输出音频，传了反而可能报错
        # - turn_detection 设 null 才是 manual 模式（有值等于开了上游 VAD）
        #
        # corpus.text 是官方给的"上下文偏置"：把术语清单当背景喂进去，
        # 让 ASR 在识别阶段就少听错专业词（与事后的同音字纠错叠加）。
        transcription: dict[str, object] = {"language": "zh"}
        corpus = glossary_for_prompt()
        if corpus:
            transcription["corpus"] = {"text": corpus}

        await self._send(
            {
                "type": "session.update",
                "session": {
                    "input_audio_format": "pcm",
                    "sample_rate": self.sample_rate,
                    "input_audio_transcription": transcription,
                    "turn_detection": None,
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
                message = f"实时语音连接中断：{exc}"
                hint = _hint_for_upstream_error(message)
                # 把可操作的那句也带上——上游的错误文本对使用者是不透明的
                if hint:
                    message = f"{message}\n{hint}"
                await self._emit(SpeechEvent(kind="error", message=message))

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
            hint = _hint_for_upstream_error(message)
            if hint:
                message = f"{message}\n{hint}"
            await self._emit(SpeechEvent(kind="error", message=message))

        elif kind == "session.updated":
            # 配置被接受。这里不做事，但把它留住：排查"配置到底生效没有"时
            # 靠的就是这个事件——`session.created` 带的是服务端**默认值**，
            # 不反映我们发过去的 update。
            pass

    async def _emit(self, event: SpeechEvent) -> None:
        if self._on_event is not None:
            await self._on_event(event)


def _hint_for_upstream_error(message: str) -> str:
    """把上游的原始错误翻成"下一步查什么"。

    上游报错用的是它自己的术语（`1011`、`Audio format is not valid`），
    对使用者完全不透明——而语音链路的排查本来就难：连接是通的，
    报错在握手之后，看起来像"连上了又断"。

    认不出就返回空串，**别硬凑建议**——指错方向比不给更浪费时间。
    """
    lowered = message.lower()

    if "audio format" in lowered:
        return (
            "音频格式取值不对。Qwen-ASR 系列只接受 pcm / opus，"
            "而 pcm16 / pcm24 是 Omni 系列的取值——两者协议相似但取值不通用。"
        )
    if "1011" in lowered and "internal" in lowered:
        return "上游内部错误。先核对 session 配置的取值，再确认模型名与账号对该模型有权限。"
    if "unauthorized" in lowered or "401" in lowered or "invalid api key" in lowered:
        return "DASHSCOPE_API_KEY 无效或过期，去百炼控制台重新生成一把。"
    if "model" in lowered and ("not found" in lowered or "not exist" in lowered):
        return "模型名不存在。核对百炼的实时语音模型列表（如 qwen3-asr-flash-realtime）。"
    if "timeout" in lowered or "timed out" in lowered:
        return "连接超时。国内平台不要走系统代理（TRUST_ENV_PROXY=false），并确认网络能到 dashscope.aliyuncs.com。"
    return ""
