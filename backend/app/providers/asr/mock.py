"""Mock 批量转写：把你粘贴的对话文稿当作"转写结果"。

**它不假装能识别音频。** 音频文件照旧保留（时长、路径都入库），
但文本来自 `fallback_text`，并且结果里 `diarized=False` + 明确的 note——
这样用户/前端知道"说话人是你自己标的"，而不是系统识别出来的。

没有 Key 也能把整条节奏学习链路跑通，这是刻意的设计（同 M1/M3 的 Mock 思路）。
"""

from __future__ import annotations

from ...analysis.pacing import parse_transcript
from .base import BatchTranscriber, SegmentDraft, TranscriptionError, TranscriptionResult


class MockBatchTranscriber(BatchTranscriber):  # type: ignore[misc]
    name = "mock"

    async def transcribe(
        self,
        *,
        audio_path: str | None,
        fallback_text: str = "",
        language: str = "zh",
    ) -> TranscriptionResult:
        if not fallback_text.strip():
            raise TranscriptionError(
                "当前未接入文件转写：请在录音条目里粘贴面试对话文稿"
                "（格式如 `[面试官] 先做个自我介绍吧` / `[我] 我是……`），"
                "或配置 ASR_PROVIDER 与对应 Key 后重试。"
            )

        drafts = parse_transcript(fallback_text)
        if not drafts:
            raise TranscriptionError("文稿里没有解析出任何对话段落，请检查格式")

        segments = [
            SegmentDraft(
                idx=draft.idx,
                speaker=draft.speaker,
                text=draft.text,
                start_ms=draft.start_ms,
                end_ms=draft.end_ms,
            )
            for draft in drafts
        ]

        return TranscriptionResult(
            segments=segments,
            duration_ms=max((segment.end_ms or 0) for segment in segments) if segments else None,
            provider=self.name,
            diarized=False,
            note="文本来自你粘贴的文稿；时间轴按字数估算（无时间戳时）",
        )
