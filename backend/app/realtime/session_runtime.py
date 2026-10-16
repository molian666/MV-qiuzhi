"""一场语音面试的运行时。

把「音频 → VAD → 回合控制 → ASR → DeepSeek 编排 → 落库 → 事件下发」串成一条线。

三条边界（照这个来改，别混）：
1. **编排逻辑**全在 `realtime/interviewer.py`，这里只做协调，不写 prompt；
2. **落库一律短事务**：commit → 放掉 DB 连接 → 调 LLM → 再开事务落库。
   绝不在 DB 事务里等模型（本地 SQLite 单写者，长事务会把 HTTP 链路也拖住）；
3. **所有下行事件带 `seq`**，前端据此去重、续接，并能在断线重连后对齐状态。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..analysis.pressure import exceeded_time_limit, normalize_pressure, should_interrupt
from ..analysis.silence import ACTION_NUDGE, SilencePolicy, decide_silence
from ..clock import utcnow
from ..analysis.glossary import correct_transcript
from ..db import session_scope
from ..models.core import DELETED_TAG, JobTarget, QuestionBank, Resume
from ..models.session import InterviewSession, SessionTurn
from ..providers.llm.base import LLMClient, LLMError
from ..providers.speech.base import SpeechEvent, SpeechProvider
from ..repositories import count_turns, ordered_turns, turn_snapshot, turn_snapshots
from ..services import log_llm_error, log_llm_result, mark_finished
from .audio_store import AudioStore
from .interviewer import REVERSE_QUESTION_PROMPT, Interviewer
from .turn_controller import TurnController, TurnControllerConfig, TurnDecision
from .vad import EnergyVad, VadConfig, VadEvent

logger = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class VoiceRuntimeConfig:
    max_questions: int = 5
    style: str = "标准技术面"
    silence_ms: int = 900
    min_answer_ms: int = 700
    asr_wait_ms: int = 1500
    metrics_interval_ms: int = 500
    # client：前端用浏览器 speechSynthesis 播报（M1 默认）
    # server：服务端 TTS 通过 audio_chunk 下发（待接入厂商）
    audio_mode: str = "client"
    text_fallback: bool = True
    # M4：面试官画像生成的指令块（题型配比 / 追问深度 / 压力水平），为空则用默认行为
    policy_block: str = ""
    #: **每轮的指令块**（`回合序号 -> 指令块`）。为什么需要它：风格预设、连环面的"这一轮由谁问"、
    #: 压力模式、语言、题库清单都只对**开场题**生效过——开场题由 REST 用完整拼装生成，
    #: 而 WS 运行时此前每轮只注入固定的 `policy_block`，于是从第 2 题起风格/角色/语言/题库
    #: 全部丢失（"面试官越问越不像那个岗位的人"）。现在按回合取块，开场题与后续题走同一套拼装。
    #: 传空则退回 `policy_block`（老行为）。
    policy_blocks: Callable[[int], str] | None = None
    #: **每轮的面试官身份**（`回合序号 -> panel.current() 的结果`）。前端靠它显示
    #: "第 N/M 位面试官"横幅并选音色；语音回合此前不带这个信息，所以第 2 题起横幅就消失了。
    interviewer_blocks: Callable[[int], dict[str, Any]] | None = None
    # M8：按当前问题挑出的故事卡事实底稿（由调用方查库拼好，编排层不碰 DB）
    story_block: str = ""
    #: M13：题库清单（"按序问到了哪"靠它）。与文字链路同源，之前语音链路整场都拿不到，
    #: 于是选了"只用内置题库"的语音面试从第 2 题起会变成自由出题。
    bank_block: str = ""
    # M11：压力模式里"运行时能管"的两项（追问深度由文本链路管）
    #: >0 表示候选人说这么久还没停就插话（打断模式）
    interrupt_after_ms: int = 0
    #: >0 表示每题限时多少秒（限时模式）
    answer_seconds: int = 0
    #: 沉默看门狗：候选人一直不说话时提醒/过题的阈值。
    #: `None` 表示关掉（保持引入它之前的行为：沉默就无限期等下去）。
    silence_policy: SilencePolicy | None = field(default_factory=SilencePolicy)


#: 看门狗轮询间隔。1 秒足够（阈值是几十秒量级），又不会空转太频繁。
WATCHDOG_TICK_SECONDS = 1.0

#: 面试阶段。收尾不是"结束"，而是**换成候选人提问**——
#: 之前这两件事是同一个动作（发完收尾题就 mark_finished），
#: 于是 rubric 里的"主动性 / 反问质量"维度永远拿不到数据。
PHASE_INTERVIEW = "interview"
PHASE_CLOSING = "closing"
PHASE_FINISHED = "finished"


class VoiceSessionRuntime:
    def __init__(
        self,
        *,
        session_id: int,
        llm: LLMClient,
        speech: SpeechProvider,
        store: AudioStore,
        sink: EventSink,
        config: VoiceRuntimeConfig | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config or VoiceRuntimeConfig()

        self._llm = llm
        self._speech = speech
        self._store = store
        self._sink = sink
        self._interviewer = Interviewer(llm)

        self._seq = 0
        self._started_wall = utcnow()
        self._vad = EnergyVad(VadConfig(min_silence_ms=self.config.silence_ms))
        self._turns = TurnController(TurnControllerConfig(min_answer_ms=self.config.min_answer_ms))

        self._turn_start_ms = 0
        self._asr_final = ""
        self._asr_partial = ""
        self._asr_ready = asyncio.Event()
        self._turn_index = 0
        self._pending_turn_audio: str | None = None
        # 本回合语音的精确区间（来自 VAD 事件，不含尾部静音）——M9 表达分析要靠它裁静音
        self._speech_started_at_ms: int | None = None
        self._speech_ended_at_ms: int | None = None
        # 最近几帧的原始音频：VAD 判定"开口"是滞后的，需要用它们回填切片开头
        self._frame_buffer: deque[bytes] = deque(maxlen=max(4, self._vad.config.start_frames + 1))
        self._last_metrics_ms = -(10**9)
        self._busy = False
        self._closed = False

        # M11：压力模式（0 表示关着，normalize 会把 0 翻译成"这个模式没开"）
        self._pressure = normalize_pressure(
            {
                "interrupt": self.config.interrupt_after_ms > 0,
                "interrupt_after_ms": self.config.interrupt_after_ms or 12_000,
                "time_limit": self.config.answer_seconds > 0,
                "answer_seconds": self.config.answer_seconds or 90,
            }
        )
        #: 本回合开口时的**音频时长**（压力模式据此算"他说了多久"）。
        #: 用音频时长而不是墙上时钟：处理音频可能比采集快或慢，只有音频时长才等于"他说了多久"。
        self._answer_started_ms: int | None = None
        #: M14 打断续问：上一轮是"面试官插话打断"（不是用户主动结束）。
        #: 下一问必须紧贴被打断的最后一句续问；随 _submit_answer 消费后清空。
        self._last_interrupt = False

        # ---- 沉默看门狗 ----
        #: 上一次"有人说话"的时间（**墙上时钟**）。
        #:
        #: 不能用 `_now_ms()`：那个时间轴优先取音频时长，而音频一停它就不走了——
        #: 用它算"沉默多久"会永远得到 0，看门狗就成了摆设。
        self._last_voice_wall = utcnow()
        #: 当前这一轮候选人有没有开过口（决定用哪套阈值）
        self._turn_has_spoken = False
        #: 这一轮已经提醒了几次
        self._turn_nudges = 0
        #: 是否正在等候选人开口（出完题、没在忙的时候为 True）
        self._awaiting = False
        self._watchdog_task: asyncio.Task[None] | None = None
        #: 当前阶段。收尾阶段里候选人的回合是"提问"，不是"回答"——两者要分开落库，
        #: 否则评分时看不出"他问了什么"，主动性维度还是没有依据。
        self._phase = PHASE_INTERVIEW

    # ------------------------------------------------------------------ #
    # 每轮的指令块与面试官身份
    # ------------------------------------------------------------------ #
    def _policy_block_for(self, round_index: int) -> str:
        """这一轮的指令块。没给按轮取块的函数时退回固定块（老行为）。"""
        resolver = self.config.policy_blocks
        if resolver is None:
            return self.config.policy_block
        try:
            return resolver(round_index)
        except Exception:  # noqa: BLE001 - 取块失败不该让整场面试断掉
            return self.config.policy_block

    def _interviewer_for(self, round_index: int) -> dict[str, Any]:
        """这一轮由谁问（连环面会换人）。单角色面试返回空字典。"""
        resolver = self.config.interviewer_blocks
        if resolver is None:
            return {}
        try:
            return dict(resolver(round_index) or {})
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        # 先把 state 发出去，再连语音上游：前端在连接建立后**立刻**拿到配置，
        # 立即显示"已连接"。若先等 speech.start()（正常约 0.5s，上游抖动时最长
        # 15s open_timeout），手机端会看到一条"打开了却什么都没发生"的连接——
        # 这正是"看起来连上了、2 秒后却断了"的最大帮凶（客户端/网络层对静默
        # 连接最没有耐心）。
        await self._send(
            {
                "type": "state",
                "session_id": self.session_id,
                "status": "live",
                "audio_mode": self.config.audio_mode,
                "speech_provider": self._speech.name,
                "text_fallback": self.config.text_fallback,
                "input_sample_rate": self._speech.sample_rate,
                "silence_ms": self.config.silence_ms,
            }
        )
        await self._speech.start(on_event=self._on_speech_event, silence_ms=self.config.silence_ms)
        self._awaiting = True
        self._watchdog_task = asyncio.create_task(self._silence_loop(), name=f"silence-{self.session_id}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stop_watchdog()
        self._store.close()
        await self._speech.close()

    # ------------------------------------------------------------------ #
    # 沉默看门狗
    # ------------------------------------------------------------------ #
    async def _stop_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 关停时不该抛
            pass

    async def _silence_loop(self) -> None:
        """周期性检查"候选人是不是一直没说话"。

        **为什么必须单独起一个定时器**：运行时其余部分都是**音频帧驱动**的
        （见 `_maybe_emit_metrics` 的注释）。而候选人沉默时恰恰没有音频帧——
        于是"没人说话"这件事永远不会被任何代码看到，面试就无限期卡住了。
        这是"用事件驱动去检测事件缺失"的经典错误。

        任何一个 tick 出错都不能让循环死掉：它死了就等于看门狗不存在，
        而那种故障完全没有征兆。
        """
        while not self._closed:
            try:
                await asyncio.sleep(WATCHDOG_TICK_SECONDS)
                await self._check_silence()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("沉默看门狗检查失败，继续等待下一个 tick")

    async def _check_silence(self) -> None:
        if self._closed or self._busy or not self._awaiting:
            return

        policy = self.config.silence_policy
        if policy is None:
            return

        elapsed = int((utcnow() - self._last_voice_wall).total_seconds() * 1000)
        decision = decide_silence(
            silence_ms=elapsed,
            has_spoken=self._turn_has_spoken,
            nudges_sent=self._turn_nudges,
            policy=policy,
        )
        if not decision.should_act:
            return

        if decision.action == ACTION_NUDGE:
            self._turn_nudges += 1
            await self._send(
                {
                    "type": "silence",
                    "action": decision.action,
                    "elapsed_ms": decision.elapsed_ms,
                    "message": decision.message,
                }
            )
            return

        # 超时：把这一轮收掉，让面试往下走。**这是原始问题（卡住）的唯一出口。**
        self._awaiting = False
        await self._send(
            {
                "type": "silence",
                "action": decision.action,
                "elapsed_ms": decision.elapsed_ms,
                "message": decision.message,
            }
        )
        await self._end_turn(self._turns.decide_manual(self._now_ms()))

    def _note_voice_activity(self) -> None:
        """记下"刚刚有人说话"，并重置这一轮的提醒计数。"""
        self._last_voice_wall = utcnow()
        self._turn_has_spoken = True

    # ------------------------------------------------------------------ #
    # 消息分发
    # ------------------------------------------------------------------ #
    async def handle(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type") or "")
        if kind == "audio_append":
            await self.on_audio(str(message.get("pcm") or ""))
        elif kind == "text":
            await self.on_text(str(message.get("text") or ""))
        elif kind == "interrupt":
            await self.on_interrupt()
        elif kind == "control":
            await self.on_control(str(message.get("action") or ""))
        elif kind == "finish":
            await self.finish()
        elif kind == "ping":
            await self._send({"type": "pong"})
        else:
            await self._send({"type": "error", "message": f"未知消息类型：{kind}"})

    # ------------------------------------------------------------------ #
    # 音频入口
    # ------------------------------------------------------------------ #
    async def on_audio(self, pcm_b64: str) -> None:
        if self._closed or not pcm_b64:
            return
        try:
            pcm = base64.b64decode(pcm_b64)
        except (binascii.Error, ValueError):
            await self._send({"type": "error", "message": "音频分片不是合法的 base64"})
            return
        if not pcm:
            return

        await self._speech.append_audio(pcm)

        ensure_fresh = getattr(self._speech, "ensure_fresh", None)
        if callable(ensure_fresh):
            try:
                await ensure_fresh()
            except Exception as exc:  # noqa: BLE001 - 重连失败不致命，等下一片再试
                await self._send({"type": "error", "message": f"语音上游重连失败：{exc}"})

        if self._busy:
            # 正在出题/落库：只存档，不做回合判定，避免把"面试官说话"误判成用户回答
            self._store.append(pcm)
            return

        # 逐帧推进：只有按帧处理，才能把"开口的前几帧"正确归到新回合的切片里。
        # （踩过的坑：先整块写盘再处理 VAD，会让 turn_*.wav 变成空文件，
        #   因为 begin_turn 发生时这一块的音频早就写完 full.wav 了。）
        frame_bytes = self._vad.config.frame_bytes
        usable = len(pcm) - (len(pcm) % frame_bytes)
        for offset in range(0, usable, frame_bytes):
            frame = pcm[offset : offset + frame_bytes]
            self._store.append(frame)
            self._frame_buffer.append(frame)
            for event in self._vad.process(frame):
                await self._on_vad_event(event)

        if usable < len(pcm):
            # 不足一帧的尾巴先落盘、不参与判定，等下一片补齐
            self._store.append(pcm[usable:])

        decision = self._turns.on_tick(self._now_ms())
        if decision is not None:
            await self._end_turn(decision)

        await self._maybe_emit_metrics()

    async def _on_vad_event(self, event: VadEvent) -> None:
        decision = self._turns.on_vad_event(event)

        if event.kind == "speech_started":
            self._turn_start_ms = self._store.duration_ms
            self._speech_started_at_ms = event.at_ms
            # M11：开始计时（打断与限时都从这里算；基准是音频时长，见 _answer_started_ms 的说明）
            self._answer_started_ms = self._now_ms()
            # 看门狗：他开口了，沉默计时清零
            self._note_voice_activity()
            self._store.begin_turn(self._turn_index)
            # 回填"开口"的那几帧：它们已经在 full.wav 里，但语音起点必须属于本回合
            for buffered in list(self._frame_buffer)[-self._vad.config.start_frames :]:
                self._store.append_turn_only(buffered)
            # 用户开口 → 通知前端立刻停掉面试官的声音（打断）
            await self._send({"type": "barge_in", "at_ms": event.at_ms})
            await self._send({"type": "speech_state", "state": "speech_started", "at_ms": event.at_ms})

        elif event.kind == "speech_stopped":
            # at_ms 是语音实际结束点（不含尾部静音）
            self._speech_ended_at_ms = event.at_ms
            # 看门狗：**从"说完"开始算沉默**，而不是从"开口"。否则一段很长的回答
            # 会让他一停下来就被判成"沉默很久"。
            self._last_voice_wall = utcnow()
            await self._send({"type": "speech_state", "state": "speech_stopped", "at_ms": event.at_ms})
            if decision is not None:
                await self._end_turn(decision)

    async def _on_speech_event(self, event: SpeechEvent) -> None:
        if event.kind == "partial":
            self._asr_partial = event.text
            await self._send({"type": "asr_partial", "text": event.text})
        elif event.kind == "final":
            # 术语纠错：ASR 会把「亚像素」听成「亚相素」这类同音词——
            # 机器视觉术语在通用语料里太罕见，通用语言模型兜不住。
            # 纠正后的文本同时用于落库与前端显示：两处不一致会让人以为系统记错了。
            text, corrections = correct_transcript(event.text)
            self._asr_final = text
            self._asr_ready.set()
            await self._send(
                {
                    "type": "asr_final",
                    "text": text,
                    # 带上改动清单，界面能显示"改了什么"——静默替换是没法核对的
                    "corrections": [
                        {"wrong": item.wrong, "right": item.right} for item in corrections
                    ],
                }
            )
        elif event.kind == "speech_started":
            await self._send({"type": "speech_state", "state": "speech_started", "at_ms": event.at_ms})
        elif event.kind == "speech_stopped":
            await self._send({"type": "speech_state", "state": "speech_stopped", "at_ms": event.at_ms})
        elif event.kind == "error":
            await self._send({"type": "error", "message": event.message})

    async def _check_pressure(self) -> None:
        """压力模式：说太久就插话，超时就收口。

        两件事都通过**结束当前回合**实现——这在听感上就是"被打断"或"时间到了"。
        如果只是发个提示让用户自己停，那就练不到东西了。
        """
        config = self._pressure
        if not config.enabled or self._answer_started_ms is None or self._busy or self._closed:
            return

        settings = config.clamped()
        elapsed = self._now_ms() - self._answer_started_ms

        if exceeded_time_limit(elapsed, config):
            self._answer_started_ms = None
            await self._send(
                {
                    "type": "pressure",
                    "mode": "time_limit",
                    "elapsed_ms": elapsed,
                    "limit_seconds": settings.answer_seconds,
                    "message": f"限时 {settings.answer_seconds} 秒已到，进入下一题",
                }
            )
            await self._end_turn(self._turns.decide_manual(self._now_ms()))
            return

        if should_interrupt(elapsed, config):
            self._answer_started_ms = None
            # M14 打断续问：记住这一轮是"被打断"的，出下一题时要求紧贴最后一句续问
            self._last_interrupt = True
            await self._send(
                {
                    "type": "pressure",
                    "mode": "interrupt",
                    "elapsed_ms": elapsed,
                    "after_ms": settings.interrupt_after_ms,
                    "message": "面试官插话了：他会针对你刚说的内容追问",
                }
            )
            await self._end_turn(self._turns.decide_manual(self._now_ms()))

    async def _maybe_emit_metrics(self) -> None:
        # M11：压力模式的时间检查搭在这里 —— 音频帧驱动，500ms 的精度对"打断"完全够用，
        # 也就不需要再起一个定时器（少一个并发源）
        await self._check_pressure()

        audio_ms = self._store.duration_ms
        if audio_ms - self._last_metrics_ms < self.config.metrics_interval_ms:
            return
        self._last_metrics_ms = audio_ms
        await self._send(
            {
                "type": "metrics",
                "audio_ms": audio_ms,
                "speaking": self._vad.is_speaking,
                "threshold": round(self._vad.threshold, 4),
            }
        )

    # ------------------------------------------------------------------ #
    # 文本入口（语音模式的文字替身 / 直接文字作答）
    # ------------------------------------------------------------------ #
    async def on_text(self, text: str) -> None:
        if self._closed or self._busy:
            return
        content = text.strip()
        if not content:
            return
        # 看门狗：文字作答也算"他说话了"，否则打字的人会被判成沉默而收到"还在吗"
        self._note_voice_activity()
        self._store.end_turn()
        self._turns.reset()
        # 注意：这里**不**清 _speech_started_at_ms/_speech_ended_at_ms ——
        # Mock 模式下音频先到、文字后到，语音区间要等真正落库时才消费。
        await self._submit_answer(content, source="text")

    async def on_interrupt(self) -> None:
        """用户打断面试官（前端本地已停播，这里是服务端侧的状态复位）。"""
        self._turns.reset()
        self._vad.reset()
        await self._send({"type": "barge_in", "at_ms": self._now_ms(), "ack": True})

    async def on_control(self, action: str) -> None:
        if action == "end_turn":
            if self._busy:
                return
            await self._end_turn(self._turns.decide_manual(self._now_ms()))
            return
        await self._send({"type": "error", "message": f"M1 暂不支持的控制指令：{action}"})

    # ------------------------------------------------------------------ #
    # 回合收口
    # ------------------------------------------------------------------ #
    async def _end_turn(self, decision: TurnDecision) -> None:
        if self._busy or self._closed:
            return

        # M11：这一回合结束了，压力计时归零
        self._answer_started_ms = None

        # 收好这一回合的音频切片，稍后写进 session_turn.audio_path（回放要靠它）
        turn_audio = self._store.end_turn()
        self._pending_turn_audio = self._store.relative(turn_audio) if turn_audio is not None else None
        self._turns.reset()

        text = ""
        if self._asr_final.strip():
            text = self._asr_final.strip()
            self._asr_final = ""
        elif not self._speech.requires_text_fallback:
            # 真 ASR：主动 commit 并等 final（最多 asr_wait_ms）
            self._asr_ready.clear()
            await self._speech.commit()
            try:
                await asyncio.wait_for(self._asr_ready.wait(), timeout=self.config.asr_wait_ms / 1000)
            except (TimeoutError, asyncio.TimeoutError):
                pass
            text = (self._asr_final or self._asr_partial).strip()
            self._asr_final = ""
            self._asr_partial = ""

        self._turn_index += 1

        if not text:
            if self.config.text_fallback:
                await self._send(
                    {
                        "type": "awaiting_text",
                        "message": "当前未接入语音识别：请用文字提交这一轮回答（音频已录制并存档）",
                        "reason": decision.reason,
                    }
                )
            else:
                await self._send(
                    {
                        "type": "error",
                        "message": "这一轮没有拿到转写文本：请重说一次，或直接用文字提交",
                        "reason": decision.reason,
                    }
                )
            return

        # 落库用的是"语音区间"，而不是"判定时刻"：
        # 后者会多算一次静音阈值（约 900ms），M9 分析语速/停顿时会被污染。
        # 注意 WAV 切片里仍保留了尾部静音（文件已写盘，裁它不划算），
        # 需要精确区间时以这里的 start_ms/end_ms 为准。
        await self._submit_answer(text, source=decision.reason)

    # ------------------------------------------------------------------ #
    # 收尾阶段：候选人提问
    # ------------------------------------------------------------------ #
    async def _answer_reverse_question(self, text: str, *, source: str) -> None:
        """候选人提问 → 面试官作答 → 结束。

        **为什么单独一条路径**：这一轮落库要标成 `kind="question"`（不是 `answer`），
        否则评分时根本看不出"他问了什么"——而 rubric 里的主动性维度就是靠它取证的。
        """
        start_ms, end_ms = self._consume_turn_span()

        with session_scope() as db:
            session_row = db.get(InterviewSession, self.session_id)
            if session_row is None:
                await self._send({"type": "error", "message": f"会话不存在：{self.session_id}"})
                return
            if session_row.status != "live":
                await self._send(
                    {"type": "error", "message": f"会话状态为 {session_row.status}，无法继续"}
                )
                return

            turns = ordered_turns(db, self.session_id)
            snapshots = turn_snapshots(turns)
            max_questions = int(session_row.plan.get("max_questions") or self.config.max_questions)
            style = str(session_row.plan.get("style") or self.config.style)
            target = db.get(JobTarget, session_row.target_id) if session_row.target_id else None
            resume = db.get(Resume, session_row.resume_id) if session_row.resume_id else None

            question_turn = SessionTurn(
                session_id=self.session_id,
                idx=len(turns),
                speaker="candidate",
                text=text,
                kind="question",
                start_ms=start_ms,
                end_ms=end_ms,
            )
            db.add(question_turn)
            db.commit()
            db.refresh(question_turn)
            question_turn_id = question_turn.id
            snapshots.append(turn_snapshot(question_turn))

        await self._send(
            {
                "type": "candidate_turn",
                "turn_id": question_turn_id,
                "idx": len(snapshots) - 1,
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source": source,
                "kind": "question",
            }
        )

        try:
            plan, result = await self._interviewer.next_question(
                turns=snapshots,
                target=target,
                resume=resume,
                max_questions=max_questions,
                style=style,
                policy_block=self._policy_block_for(max_questions),
                story_block=self.config.story_block,
                prompt=REVERSE_QUESTION_PROMPT,
            )
        except LLMError as exc:
            with session_scope() as db:
                log_llm_error(
                    db,
                    task="interview_turn",
                    provider=getattr(self._llm, "name", ""),
                    error=str(exc),
                    session_id=self.session_id,
                )
            # 答不上来也要把面试收掉，不能卡在"等他提问"的阶段不动
            await self._finish_closing()
            await self._send({"type": "error", "message": f"生成回答失败：{exc}"})
            return

        with session_scope() as db:
            log_llm_result(db, result, session_id=self.session_id)
            answer_turn = SessionTurn(
                session_id=self.session_id,
                idx=len(snapshots),
                speaker="interviewer",
                text=plan.question,
                kind="closing",
                topic=plan.topic,
                start_ms=end_ms,
                end_ms=end_ms,
            )
            db.add(answer_turn)
            db.commit()
            db.refresh(answer_turn)
            answer_turn_id = answer_turn.id

        await self._send(
            {
                "type": "interviewer_text",
                "turn_id": answer_turn_id,
                "idx": len(snapshots),
                "text": plan.question,
                "kind": "closing",
                "topic": plan.topic,
                "is_followup": False,
                "coach": None,
                "interviewer": self._interviewer_for(max_questions),
            }
        )
        await self._finish_closing()

    async def _finish_closing(self) -> None:
        """真正结束会话。**只在收尾阶段走完（或失败）之后调用。**"""
        self._phase = PHASE_FINISHED
        await self._stop_watchdog()
        with session_scope() as db:
            session_row = db.get(InterviewSession, self.session_id)
            if session_row is not None:
                mark_finished(db, session_row)
        await self._emit_finished(manual=False)

    # ------------------------------------------------------------------ #
    # 出题 + 落库（两段式，中间不持有 DB 连接）
    # ------------------------------------------------------------------ #
    async def _submit_answer(self, text: str, *, source: str, is_closing: bool = False) -> None:
        self._busy = True
        # 出题期间不再接受沉默判定：`_check_silence` 也会看 _busy，
        # 但这里显式清掉 awaiting 更直白——"正在出题"本身就是不等待。
        self._awaiting = False
        self._turn_has_spoken = False
        self._turn_nudges = 0
        #: 这一轮是不是"最后一题"。**必须做参数**：以前它在 try 里被赋值，
        #: 而收尾阶段的分支会提前 return，于是 finally 里读它直接 UnboundLocalError。
        closing = is_closing
        try:
            # 收尾阶段：他是在**提问**，不是在答题。走单独一条路径——
            # 落库的 kind 不同（评分要看得出他问了什么），出话的指令也不同（要回答他）。
            if self._phase == PHASE_CLOSING:
                await self._answer_reverse_question(text, source=source)
                return

            start_ms, end_ms = self._consume_turn_span()

            # 阶段 1：落候选人回合 + 取出构造 prompt 所需的快照
            with session_scope() as db:
                session_row = db.get(InterviewSession, self.session_id)
                if session_row is None:
                    await self._send({"type": "error", "message": f"会话不存在：{self.session_id}"})
                    return
                if session_row.status != "live":
                    await self._send({"type": "error", "message": f"会话状态为 {session_row.status}，无法继续作答"})
                    return

                turns = ordered_turns(db, self.session_id)
                snapshots = turn_snapshots(turns)
                max_questions = int(session_row.plan.get("max_questions") or self.config.max_questions)
                style = str(session_row.plan.get("style") or self.config.style)
                # M14 现场突发：从 plan 读开关（语音链路与文字链路同一口径）
                scene_enabled = bool(session_row.plan.get("scene"))
                target = db.get(JobTarget, session_row.target_id) if session_row.target_id else None
                resume = db.get(Resume, session_row.resume_id) if session_row.resume_id else None

                _, answer_count = count_turns(turns)
                idx = len(turns)
                # 音频切片用完即清：否则第二个回答会错误地复用第一段的录音
                audio_path = self._pending_turn_audio
                self._pending_turn_audio = None
                answer_turn = SessionTurn(
                    session_id=self.session_id,
                    idx=idx,
                    speaker="candidate",
                    text=text,
                    kind="answer",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    audio_path=audio_path,
                )
                db.add(answer_turn)
                db.commit()
                db.refresh(answer_turn)
                answer_turn_id = answer_turn.id
                snapshots.append(turn_snapshot(answer_turn))
                answer_count += 1
                closing = answer_count >= max_questions
            await self._send(
                {
                    "type": "candidate_turn",
                    "turn_id": answer_turn_id,
                    "idx": idx,
                    "text": text,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "source": source,
                }
            )

            # 阶段 2：出题（这里不持有任何 DB 连接）
            interrupted = self._last_interrupt
            self._last_interrupt = False
            # M14 现场突发：第 3 次回答之后、未到收尾时，强制下一问为突发场景题
            force_scene = scene_enabled and answer_count == 3 and not closing
            # 这一轮该由谁问：身份与指令块都按回合取，让风格/角色/语言/题库在整场都成立，
            # 而不是只对开场题成立（这是"语音面试比文字面试笨"的根因）
            round_index = answer_count
            turn_policy_block = self._policy_block_for(round_index)
            turn_interviewer = self._interviewer_for(round_index)
            try:
                plan, result = await self._interviewer.next_question(
                    turns=snapshots,
                    target=target,
                    resume=resume,
                    max_questions=max_questions,
                    style=style,
                    closing=closing,
                    story_block=self.config.story_block,
                    policy_block=turn_policy_block,
                    interrupted=interrupted,
                    force_scene=force_scene,
                    bank_block=self.config.bank_block,
                )
            except LLMError as exc:
                with session_scope() as db:
                    log_llm_error(
                        db,
                        task="interview_turn",
                        provider=getattr(self._llm, "name", ""),
                        error=str(exc),
                        session_id=self.session_id,
                    )
                # 兜底：模型连续失败不能让面试卡死。从题库抽一道与岗位相关的题顶上，
                # 面试继续走；只有题库也为空时才发 error 事件。
                fallback = self._pick_fallback_question(target)
                if fallback is None:
                    await self._send({"type": "error", "message": f"生成问题失败：{exc}"})
                    return
                fallback_text, fallback_topic = fallback
                fallback_kind = "closing" if closing else "new_topic"
                with session_scope() as db:
                    question_turn = SessionTurn(
                        session_id=self.session_id,
                        idx=len(snapshots),
                        speaker="interviewer",
                        text=fallback_text,
                        kind=fallback_kind,
                        topic=fallback_topic,
                        is_followup=False,
                        # 记下这是兜底题（前端的 interviewer_text 不读 meta，仅留痕）
                        meta={"fallback": True, "reason": str(exc)[:200]},
                        start_ms=end_ms,
                        end_ms=end_ms,
                    )
                    db.add(question_turn)
                    db.commit()
                    db.refresh(question_turn)
                    question_turn_id = question_turn.id
                    if closing:
                        self._phase = PHASE_CLOSING
                    after = ordered_turns(db, self.session_id)
                    question_count, answer_total = count_turns(after)
                await self._send(
                    {
                        "type": "interviewer_text",
                        "turn_id": question_turn_id,
                        "idx": len(snapshots),
                        "text": fallback_text,
                        "kind": fallback_kind,
                        "topic": fallback_topic,
                        "is_followup": False,
                        "coach": None,
                        "interviewer": turn_interviewer,
                    }
                )
                await self._send(
                    {
                        "type": "turn_meta",
                        "question_count": question_count,
                        "answer_count": answer_total,
                        "max_questions": max_questions,
                        "audio_ms": self._store.duration_ms,
                    }
                )
                if closing:
                    await self._send(
                        {
                            "type": "awaiting_question",
                            "message": "轮到你提问了：有什么想了解的可以直接说（没有的话点「结束面试」）。",
                        }
                    )
                return

            kind = "closing" if closing else (plan.kind or "new_topic")

            # 阶段 3：落面试官回合（+ 可能的收尾）
            with session_scope() as db:
                log_llm_result(db, result, session_id=self.session_id)
                question_turn = SessionTurn(
                    session_id=self.session_id,
                    idx=len(snapshots),
                    speaker="interviewer",
                    text=plan.question,
                    kind=kind,
                    topic=plan.topic,
                    is_followup=plan.is_followup and not closing,
                    # 把"这一轮由谁问"并进 meta：前端靠它显示第 N/M 位面试官并选音色。
                    # 之前语音回合不写 interviewer，于是连环面横幅在第 2 题后消失。
                    meta={**plan.raw, **({"interviewer": turn_interviewer} if turn_interviewer else {})},
                    start_ms=end_ms,
                    end_ms=end_ms,
                )
                db.add(question_turn)
                db.commit()
                db.refresh(question_turn)
                question_turn_id = question_turn.id

                if closing:
                    # **进入收尾阶段，而不是结束面试**：接下来是候选人提问的环节，
                    # 会话必须保持 live（否则前端会把他锁在输入框外，主动性维度永远没数据）。
                    self._phase = PHASE_CLOSING

                after = ordered_turns(db, self.session_id)
                question_count, answer_total = count_turns(after)

            await self._send(
                {
                    "type": "interviewer_text",
                    "turn_id": question_turn_id,
                    "idx": len(snapshots),
                    "text": plan.question,
                    "kind": kind,
                    "topic": plan.topic,
                    "is_followup": plan.is_followup and not closing,
                    # M14 教练模式：把点评随问题一起下发，前端在同一回合下展示复盘卡。
                    # 空点评不发（None 会被 JSON 序列化成 null，前端按 null 处理）
                    "coach": plan.coach,
                    # 连环面换人：前端据此更新顶部横幅与音色；单角色时为空字典
                    "interviewer": turn_interviewer,
                }
            )
            await self._send(
                {
                    "type": "turn_meta",
                    "question_count": question_count,
                    "answer_count": answer_total,
                    "max_questions": max_questions,
                    "audio_ms": self._store.duration_ms,
                }
            )
            if closing:
                # 收尾题发出去了，但**面试没结束**：现在等候选人提问。
                # 这里不改 phase（上面已经设成 closing），但要继续等他开口，
                # 否则收尾阶段没人说话时看门狗不会推动。
                await self._send(
                    {
                        "type": "awaiting_question",
                        "message": "轮到你提问了：有什么想了解的可以直接说（没有的话点「结束面试」）。",
                    }
                )
        finally:
            self._busy = False
            if not closing:
                # 题已经问出来了，重新开始等候选人开口（看门狗从这里接着算）
                self._awaiting = True
                self._last_voice_wall = utcnow()
                self._turn_has_spoken = False
                self._turn_nudges = 0

    async def finish(self) -> None:
        if self._closed:
            return
        self._store.end_turn()
        with session_scope() as db:
            session_row = db.get(InterviewSession, self.session_id)
            if session_row is not None:
                mark_finished(db, session_row)
        await self._emit_finished(manual=True)

    async def _emit_finished(self, *, manual: bool) -> None:
        audio_path = self._store.full_path
        has_audio = audio_path.exists() and self._store.total_bytes > 0
        await self._send(
            {
                "type": "finished",
                "session_id": self.session_id,
                "manual": manual,
                "audio_ms": self._store.duration_ms,
                "audio_available": has_audio,
            }
        )

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _pick_fallback_question(self, target: JobTarget | None) -> tuple[str, str] | None:
        """模型连续失败时的题库兜底：抽一道与岗位相关的开放题顶上，避免面试卡死。

        优先级：岗位 title/tags 命中 > 任意开放题。返回 (题目文本, 主题)；
        题库为空（或全是选择题/已删除）时返回 None，调用方再发 error 事件。
        """
        with session_scope() as db:
            rows = db.query(QuestionBank).filter(
                QuestionBank.question_type == "open",
                QuestionBank.text != "",
            ).all()
            candidates = [q for q in rows if DELETED_TAG not in (q.tags or [])]
            if not candidates:
                return None

            if target is not None:
                keywords = [k for k in [target.title, *(target.tags or [])] if k]

                def hit(q: QuestionBank) -> int:
                    blob = f"{q.text} {' '.join(q.tags or [])}"
                    return sum(1 for k in keywords if k and k.lower() in blob.lower())

                score, best = max(((hit(q), q) for q in candidates), key=lambda p: p[0])
                if score > 0 and best.text.strip():
                    return best.text.strip(), "通用技术题"

            pick = random.choice(candidates)
            return pick.text.strip(), "通用技术题"

    def _now_ms(self) -> int:
        """会话内时间轴：优先用音频时间（单调、可与波形对齐），无音频时退回墙钟。"""
        audio_ms = self._store.duration_ms
        if audio_ms > 0:
            return audio_ms
        return max(0, int((utcnow() - self._started_wall).total_seconds() * 1000))

    def _consume_turn_span(self) -> tuple[int, int]:
        """取走本轮回答的语音区间（取走即清空）。

        优先用 VAD 给出的精确语音起止（不含尾部静音）；纯文字作答时退回当前时间。
        """
        if self._speech_started_at_ms is not None:
            start = self._speech_started_at_ms
            end = self._speech_ended_at_ms if self._speech_ended_at_ms is not None else self._now_ms()
        else:
            start = end = self._now_ms()

        self._speech_started_at_ms = None
        self._speech_ended_at_ms = None
        return start, end

    async def _send(self, event: dict[str, Any]) -> None:
        self._seq += 1
        event["seq"] = self._seq
        await self._sink(event)
