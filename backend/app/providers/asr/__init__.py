"""批量转写提供方。新增一家 = 新增一个实现 BatchTranscriber 的模块。"""

from .base import (
    BatchTranscriber,
    SegmentDraft,
    TranscriptionError,
    TranscriptionResult,
)
from .mock import MockBatchTranscriber
from .qwen_file import QwenFileTranscriber

__all__ = [
    "BatchTranscriber",
    "MockBatchTranscriber",
    "QwenFileTranscriber",
    "SegmentDraft",
    "TranscriptionError",
    "TranscriptionResult",
]
