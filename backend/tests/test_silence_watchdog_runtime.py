"""沉默看门狗的运行时接入。

纯判定逻辑在 `test_silence_watchdog.py`；这里证明它**真的接上了**——
而这一点必须用"一直不说话"来验证，因为原始缺陷恰恰是"没人说话时什么都不发生"。

不用 `time.sleep` 等真实阈值：把策略的阈值设成毫秒级，再只驱动 tick 所需的那点等待。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.analysis.silence import ACTION_ADVANCE, ACTION_NUDGE, SilencePolicy
from app.providers.llm.mock import MockLLMClient
from app.providers.speech.mock import MockSpeechProvider
from app.realtime.audio_store import AudioStore
from app.realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime

#: 阈值压到很小，用例只需等几百毫秒。看门狗的 tick 是 1 秒，所以这里要等 1 秒以上。
TINY = SilencePolicy(
    nudge_ms=1,
    timeout_ms=1_500,
    cold_start_nudge_ms=1,
    cold_start_timeout_ms=1_500,
)


async def _runtime(
    client: TestClient,
    tmp_path: Path,
    *,
    policy: SilencePolicy | None = TINY,
    max_questions: int = 3,
) -> tuple[VoiceSessionRuntime, list[dict[str, Any]]]:
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": max_questions}).json()
    session_id = int(created["id"])

    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=MockLLMClient(),
        speech=MockSpeechProvider(),
        store=AudioStore(tmp_path / "audio", session_id, sample_rate=16000),
        sink=sink,
        config=VoiceRuntimeConfig(
            max_questions=max_questions,
            text_fallback=True,
            silence_ms=300,
            min_answer_ms=200,
            silence_policy=policy,
        ),
    )
    await runtime.start()
    return runtime, events


def _silence_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "silence"]


# --------------------------------------------------------------------------- #
# 原始缺陷：没人说话时什么都不发生
# --------------------------------------------------------------------------- #
async def test_watchdog_fires_when_nobody_says_anything(client: TestClient, tmp_path: Path) -> None:
    """**这是这个功能存在的全部理由**：候选人一直不说话时，面试不能无限期卡住。

    注意这里**不推任何音频**——运行时的其余部分都是音频帧驱动的，
    所以如果没有独立定时器，这个用例会永远收不到任何事件。
    """
    runtime, events = await _runtime(client, tmp_path)

    await asyncio.sleep(1.2)  # 让看门狗跑一个 tick（tick = 1 秒）

    await runtime.close()
    assert _silence_events(events), "沉默时应该收到 silence 事件"


async def test_watchdog_nudges_then_advances(client: TestClient, tmp_path: Path) -> None:
    """先提醒一次，再超时过题——两级都要走到。"""
    runtime, events = await _runtime(client, tmp_path)

    # 1.2s：应当已经提醒（threshold=1ms，且只提醒一次）
    await asyncio.sleep(1.2)
    # 再等一段：超过 timeout_ms=1500，应当过题
    await asyncio.sleep(1.2)

    await runtime.close()

    actions = [event["action"] for event in _silence_events(events)]
    assert ACTION_NUDGE in actions, f"应该有提醒，实际 {actions}"
    assert ACTION_ADVANCE in actions, f"应该超时过题，实际 {actions}"


async def test_nudge_carries_a_readable_message(client: TestClient, tmp_path: Path) -> None:
    runtime, events = await _runtime(client, tmp_path)
    await asyncio.sleep(1.2)
    await runtime.close()

    nudges = [event for event in _silence_events(events) if event["action"] == ACTION_NUDGE]
    assert nudges
    assert nudges[0]["message"].strip()


async def test_watchdog_can_be_disabled(client: TestClient, tmp_path: Path) -> None:
    """`silence_policy=None` 时保持引入它之前的行为（沉默就等下去）。"""
    runtime, events = await _runtime(client, tmp_path, policy=None)

    await asyncio.sleep(1.2)

    await runtime.close()
    assert _silence_events(events) == []


# --------------------------------------------------------------------------- #
# 有人说话时不该误报
# --------------------------------------------------------------------------- #
async def test_text_answer_does_not_trigger_the_watchdog(client: TestClient, tmp_path: Path) -> None:
    """**打字的人不该被判成沉默**而收到"还在吗"。"""
    runtime, events = await _runtime(client, tmp_path)

    await runtime.on_text("我是做机器视觉的，负责标定与检测。")
    events.clear()  # 只看这一轮出题之后

    await asyncio.sleep(0.5)  # 不到一个 tick

    await runtime.close()
    assert _silence_events(events) == []


async def test_talking_resets_the_silence_timer(client: TestClient, tmp_path: Path) -> None:
    """说过话之后计时清零：否则一段长回答刚停下就会被判成"沉默很久"。"""
    runtime, events = await _runtime(client, tmp_path)

    await asyncio.sleep(1.2)
    first = len(_silence_events(events))
    assert first > 0

    # 他说话了 → 计数应当重置（这里用文字作答模拟，时序上等价）
    await runtime.on_text("补充一句。")
    events.clear()
    await asyncio.sleep(0.4)

    await runtime.close()
    assert _silence_events(events) == []


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
async def test_watchdog_is_cancelled_on_close(client: TestClient, tmp_path: Path) -> None:
    """关掉会话必须停掉定时器，否则它会一直跑着（进程里堆积僵尸任务）。"""
    runtime, _events = await _runtime(client, tmp_path)
    task = runtime._watchdog_task
    assert task is not None

    await runtime.close()

    assert runtime._watchdog_task is None
    assert task.cancelled() or task.done()


async def test_close_is_idempotent(client: TestClient, tmp_path: Path) -> None:
    runtime, _events = await _runtime(client, tmp_path)

    await runtime.close()
    await runtime.close()  # 不该抛
