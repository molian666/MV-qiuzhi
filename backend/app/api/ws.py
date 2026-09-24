"""WebSocket 端点：`/ws/sessions/{id}`（M1 实时语音链路）。

设计：会话本身由 REST 创建（`POST /api/sessions`，mode=realtime），开场题已经落库；
WS 只负责"音频双向 + 事件下发"，因此 M0 的文字链路与 M1 的语音链路共用同一套落库与编排。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..analysis.pacing import policy_prompt_block
from ..config import get_settings
from ..db import session_scope
from ..deps import get_llm, get_speech_provider
from ..models.session import InterviewSession
from ..pacing_service import load_policy_for_session
from ..realtime.audio_store import AudioStore
from ..analysis.pressure import normalize_pressure
from ..auth import websocket_authenticated
from ..realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime
from ..story_service import starred_block
router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)


@router.websocket("/ws/sessions/{session_id}")
async def session_ws(websocket: WebSocket, session_id: int) -> None:
    await websocket.accept()
    if not websocket_authenticated(websocket):
        # 先 accept 再发可读原因再关：未 accept 直接关只能得到裸 403，
        # 前端分不清「没登录」和「服务挂了」。4401 与 HTTP 401 语义对齐。
        await websocket.send_json({"type": "error", "message": "未登录或登录已过期，请重新登录后再连语音"})
        await websocket.close(code=4401)
        return
    settings = get_settings()

    with session_scope() as db:
        session_row = db.get(InterviewSession, session_id)
        if session_row is None:
            await websocket.send_json({"type": "error", "message": f"会话不存在：{session_id}"})
            await websocket.close(code=4404)
            return
        plan = dict(session_row.plan or {})
        status = session_row.status
        # M4：选了面试官画像就按它的节奏来——等待时长直接覆盖 VAD 的静音阈值
        policy = load_policy_for_session(db, session_row, default_silence_ms=settings.speech_silence_ms)

    if status != "live":
        await websocket.send_json({"type": "error", "message": f"会话状态为 {status}，无法继续"})
        await websocket.close(code=4409)
        return

    speech = get_speech_provider()
    llm = get_llm()
    store = AudioStore(settings.audio_dir, session_id, sample_rate=speech.sample_rate)

    # M11：压力模式（语音这边只管打断与限时；追问深度由文本链路管）
    pressure = normalize_pressure(plan.get("pressure"))

    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=llm,
        speech=speech,
        store=store,
        sink=websocket.send_json,
        config=VoiceRuntimeConfig(
            max_questions=int(plan.get("max_questions") or settings.default_max_questions),
            style=str(plan.get("style") or "标准技术面"),
            # 画像里的等待时长覆盖默认 VAD 阈值：这是"节奏跟着画像变"最直接的体现
            silence_ms=policy.silence_ms if policy is not None else settings.speech_silence_ms,
            min_answer_ms=settings.min_answer_ms,
            asr_wait_ms=settings.asr_wait_ms,
            # Mock 语音（没有 ASR）时必须允许文字替身，否则这一轮没法继续
            text_fallback=speech.requires_text_fallback,
            policy_block=policy_prompt_block(policy) if policy is not None else "",
            # M8：语音会话的底稿在开始时固定，所以给"主推的故事卡"而不是每轮重挑
            story_block=starred_block(db),
            # M11：0 表示这个模式没开
            interrupt_after_ms=(pressure.clamped().interrupt_after_ms if pressure.interrupt else 0),
            answer_seconds=(pressure.clamped().answer_seconds if pressure.time_limit else 0),
        ),
    )

    try:
        await runtime.start()
        while True:
            message = await websocket.receive_json()
            await runtime.handle(message)
    except WebSocketDisconnect:
        logger.info("会话 %s 的 WebSocket 已断开", session_id)
    except Exception as exc:  # noqa: BLE001 - 兜底：把异常告诉前端，别静默断
        logger.exception("语音会话 %s 出错", session_id)
        try:
            await websocket.send_json({"type": "error", "message": f"服务端异常：{exc}"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        await runtime.close()
