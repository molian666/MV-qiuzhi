"""百炼录音文件转写（含说话人分离）——**尚未接入**。

为什么不先写一版"看起来能用"的实现：
DashScope 的录音文件识别要走「申请上传凭证 → 上传音频拿 file_url → 异步提交任务 → 轮询结果」
这条链路，还要处理说话人分离的参数与结果结构。这些细节我**没有用真实 Key 验证过**，
凭空写出来的代码只会在你第一次真机跑的时候崩，而且会让人误以为"已经接好了"。

对比一下：M1 的 `QwenRealtimeSpeechProvider` 敢写，是因为它的协议（OpenAI-Realtime 方言、
事件名、手shake 流程）我当时逐条核实过官方文档；文件转写的上传链路我没有核实，所以这里只留接口。

接入步骤（等你提供 DASHSCOPE_API_KEY 时我来做 + 真机验证）：
1. 用 `ASR_PROVIDER=qwen-file` + `DASHSCOPE_API_KEY` 打开本类；
2. 按官方文档补完「上传 → 提交异步任务 → 轮询 → 解析说话人分离结果」；
3. 把结果映射成 `SegmentDraft(speaker=interviewer|candidate, start_ms, end_ms)`；
4. 补一条真机测试记录到 docs/PLAN.md 的 §6.2。
"""

from __future__ import annotations

from .base import BatchTranscriber, TranscriptionError, TranscriptionResult


class QwenFileTranscriber(BatchTranscriber):  # type: ignore[misc]
    name = "qwen-file"

    def __init__(self, *, api_key: str, model: str = "paraformer-v2", endpoint: str = "") -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint

    async def transcribe(
        self,
        *,
        audio_path: str | None,
        fallback_text: str = "",
        language: str = "zh",
    ) -> TranscriptionResult:
        if not self._api_key:
            raise TranscriptionError("未配置 DASHSCOPE_API_KEY")
        raise TranscriptionError(
            "百炼录音文件转写尚未接入（需要真实 Key 校准上传与异步任务流程）。"
            "现在请把 ASR_PROVIDER 设为 mock 并在录音条目里粘贴对话文稿。"
        )
