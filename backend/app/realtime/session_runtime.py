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
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..clock import utcnow
from ..db import session_scope
from ..models.core import JobTarget, Resume
from ..models.session import InterviewSession, SessionTurn
from ..providers.llm.base import LLMClient, LLMError
from ..providers.speech.base import SpeechEvent, SpeechProvider
from ..repositories import count_turns, ordered_turns, turn_snapshot, turn_snapshots
from ..services import log_llm_error, log_llm_result, mark_finished
from .audio_store import AudioStore
from .interviewer import Interviewer
from .turn_controller import TurnController, TurnControllerConfig, TurnDecision
from .vad import EnergyVad, VadConfig, VadEvent

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
    # M8：按当前问题挑出的故事卡事实底稿（由调用方查库拼好，编排层不碰 DB）
    story_block: str = ""


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

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        await self._speech.start(on_event=self._on_speech_event, silence_ms=self.config.silence_ms)
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

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._store.close()
        await self._speech.close()

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
            await self._send({"type": "speech_state", "state": "speech_stopped", "at_ms": event.at_ms})
            if decision is not None:
                await self._end_turn(decision)

    async def _on_speech_event(self, event: SpeechEvent) -> None:
        if event.kind == "partial":
            self._asr_partial = event.text
            await self._send({"type": "asr_partial", "text": event.text})
        elif event.kind == "final":
            self._asr_final = event.text
            self._asr_ready.set()
            await self._send({"type": "asr_final", "text": event.text})
        elif event.kind == "speech_started":
            await self._send({"type": "speech_state", "state": "speech_started", "at_ms": event.at_ms})
        elif event.kind == "speech_stopped":
            await self._send({"type": "speech_state", "state": "speech_stopped", "at_ms": event.at_ms})
        elif event.kind == "error":
            await self._send({"type": "error", "message": event.message})

    async def _maybe_emit_metrics(self) -> None:
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
    # 出题 + 落库（两段式，中间不持有 DB 连接）
    # ------------------------------------------------------------------ #
    async def _submit_answer(self, text: str, *, source: str) -> None:
        self._busy = True
        try:
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
            try:
                plan, result = await self._interviewer.next_question(
                    turns=snapshots,
                    target=target,
                    resume=resume,
                    max_questions=max_questions,
                    style=style,
                    closing=closing,
                    story_block=self.config.story_block,
                    policy_block=self.config.policy_block,
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
                await self._send({"type": "error", "message": f"生成问题失败：{exc}"})
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
                    meta=plan.raw,
                    start_ms=end_ms,
                    end_ms=end_ms,
                )
                db.add(question_turn)
                db.commit()
                db.refresh(question_turn)
                question_turn_id = question_turn.id

                if closing:
                    session_row = db.get(InterviewSession, self.session_id)
                    if session_row is not None:
                        mark_finished(db, session_row)

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
                await self._emit_finished(manual=False)
        finally:
            self._busy = False

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
