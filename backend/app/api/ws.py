"""WebSocket 端点：`/ws/sessions/{id}`（M1 实时语音链路）。

设计：会话本身由 REST 创建（`POST /api/sessions`，mode=realtime），开场题已经落库；
WS 只负责"音频双向 + 事件下发"，因此 M0 的文字链路与 M1 的语音链路共用同一套落库与编排。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..db import session_scope
from ..deps import get_llm, get_speech_provider
from ..models.session import InterviewSession
from ..pacing_service import load_policy_for_session
from ..realtime.audio_store import AudioStore
from ..auth import websocket_authenticated
from ..realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime
from ..story_service import starred_block
from .sessions import _bank_block_from_session, _compose_policy_block, _realism_flags, _session_options
router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)


@router.websocket("/ws/sessions/{session_id}")
async def session_ws(websocket: WebSocket, session_id: int) -> None:
    await websocket.accept()
    # 记录连接起点与是否进入消息循环：断开日志里带上持续时长和所处阶段，
    # 才能区分「网络层掐断」（loop 已进入，突然 1005）与「启动阶段被断」
    # （state 都没发出去就被断，多为服务端上游初始化慢）。
    connected_at = time.monotonic()
    loop_entered = False
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
        # M8：语音会话的底稿在开始时固定，所以给"主推的故事卡"而不是每轮重挑。
        # **必须在事务里取**：出了 `with` 之后 `db` 已经关闭，再用它只会抛异常。
        story_block = starred_block(db)
        # M11/M13/M14：把这些从 plan 里还原出来（此前只在开场题那一刻算过）
        pressure, panel, style_preset, locale_key = _session_options(session_row)
        coach, silence_pressure, scene, resume_dive = _realism_flags(session_row)
        bank_block = _bank_block_from_session(session_row)

    if status != "live":
        await websocket.send_json({"type": "error", "message": f"会话状态为 {status}，无法继续"})
        await websocket.close(code=4409)
        return

    speech = get_speech_provider()
    llm = get_llm()
    store = AudioStore(settings.audio_dir, session_id, sample_rate=speech.sample_rate)

    max_questions = int(plan.get("max_questions") or settings.default_max_questions)
    #: 开场题已经问过（round 0 是创建会话时生成的），所以后续回合从 1 开始。
    #: 多算 1 轮是为了收尾那一问；连环面的阶段数可能超过题数，取两者较大值。
    panel_rounds = int((plan.get("panel") or {}).get("total_rounds") or 0)
    block_count = max(max_questions, panel_rounds) + 1

    def policy_blocks(round_index: int) -> str:
        """这一轮的完整指令块：风格 + 连环面身份 + 压力 + 拟真 + 画像 + 语言。

        与文字链路走**同一个** `_compose_policy_block`，所以两条链路不会再各写一套、
        也不会再出现"语音面试从第 2 题起丢掉风格/角色/语言"这种情况。
        """
        round_style = panel.style_for(round_index) if panel is not None else style_preset
        return _compose_policy_block(
            style=round_style,
            panel=panel,
            panel_round=round_index,
            pressure=pressure,
            policy=policy,
            locale=locale_key,
            coach=coach,
            silence_pressure=silence_pressure,
            scene=scene,
            resume_dive=resume_dive,
        )

    def interviewer_blocks(round_index: int) -> dict:
        return panel.current(round_index) if panel is not None else {}

    # 开场题（round 0）的块同时留作兜底：万一按轮取块出问题，退回老行为而不是丢掉全部指令
    fallback_block = policy_blocks(0)

    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=llm,
        speech=speech,
        store=store,
        sink=websocket.send_json,
        config=VoiceRuntimeConfig(
            max_questions=max_questions,
            style=str(plan.get("style") or "标准技术面"),
            # 画像里的等待时长覆盖默认 VAD 阈值：这是"节奏跟着画像变"最直接的体现
            silence_ms=policy.silence_ms if policy is not None else settings.speech_silence_ms,
            min_answer_ms=settings.min_answer_ms,
            asr_wait_ms=settings.asr_wait_ms,
            # Mock 语音（没有 ASR）时必须允许文字替身，否则这一轮没法继续
            text_fallback=speech.requires_text_fallback,
            policy_block=fallback_block,
            policy_blocks=policy_blocks,
            interviewer_blocks=interviewer_blocks,
            story_block=story_block,
            bank_block=bank_block,
            # M11：0 表示这个模式没开
            interrupt_after_ms=(pressure.clamped().interrupt_after_ms if pressure.interrupt else 0),
            answer_seconds=(pressure.clamped().answer_seconds if pressure.time_limit else 0),
        ),
    )

    try:
        await runtime.start()
        logger.info("会话 %s 的 WebSocket 连接已建立并进入消息循环", session_id)
        loop_entered = True
        while True:
            message = await websocket.receive_json()
            await runtime.handle(message)
    except WebSocketDisconnect as exc:
        # 把断开原因记下来：code=1000 是正常关闭，1001/1006 多为对端或网络层断开，
        # 1005 在本 uvicorn 版本里表示"对端没发关闭帧连接就没了"（网络被掐）。
        # 之前这里只记一句"已断开"，看不到 code/reason，静默断开时无法定位是浏览器
        # 主动关、网络设备掐断还是服务端 ping 超时（uvicorn 默认 20s 无 pong 会掐）。
        stage = "，未进入消息循环（启动阶段被断）" if not loop_entered else ""
        logger.warning(
            "会话 %s 的 WebSocket 已断开：code=%s reason=%r（连接持续 %.1fs%s）",
            session_id,
            getattr(exc, "code", None),
            getattr(exc, "reason", None),
            time.monotonic() - connected_at,
            stage,
        )
    except Exception as exc:  # noqa: BLE001 - 兜底：把异常告诉前端，别静默断
        logger.exception("语音会话 %s 出错", session_id)
        try:
            await websocket.send_json({"type": "error", "message": f"服务端异常：{exc}"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        await runtime.close()
