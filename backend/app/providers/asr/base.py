"""批量转写（含说话人分离）提供方抽象。

与 `providers/speech`（实时语音）的分工：
- speech：低延迟、边听边出字，服务模拟面试的对话回合；
- asr（这里）：把一整段录音事后转成"带说话人与时间轴的段落"，服务节奏分析。

之所以分开：两者的接口形状与失败模式完全不同，硬塞进一个抽象会两边都别扭。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class SegmentDraft:
    idx: int
    speaker: str  # interviewer | candidate | unknown
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    confidence: float | None = None


@dataclass(slots=True)
class TranscriptionResult:
    segments: list[SegmentDraft] = field(default_factory=list)
    duration_ms: int | None = None
    provider: str = ""
    #: 是否真的做了说话人分离（Mock 从文稿解析时是 False，需要如实告知用户）
    diarized: bool = False
    note: str = ""


class TranscriptionError(RuntimeError):
    """转写失败（没有文稿、上游不可用、格式不支持等）。"""


@runtime_checkable
class BatchTranscriber(Protocol):
    name: str

    async def transcribe(
        self,
        *,
        audio_path: str | None,
        fallback_text: str = "",
        language: str = "zh",
    ) -> TranscriptionResult: ...
