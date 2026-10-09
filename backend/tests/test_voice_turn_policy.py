"""语音链路的**每轮**指令补全（M11/M13/M14 的一次回归修复）。

这一组用例针对的是一个真实缺陷：`POST /api/sessions` 生成开场题时用的是文字链路的完整
拼装（风格 → 连环面 → 压力 → 拟真 → 画像 → 语言 → 题库清单），但 WS 运行时此前每轮
只注入固定的 `policy_block`（拟真 + 画像）。结果是——用户选了「压力面 / 连环面 / English /
只用内置题库」，**这些只对开场题生效**，从第 2 题起全部丢失，而且语音回合的 meta 不带
`interviewer`，前端的「第 N/M 位面试官」横幅也在第 2 题后消失。

所以这里断言的重点是"**第 2 轮及以后**"，不是第 1 轮：只测第 1 轮会让这个 bug 继续藏下去。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.providers.llm.base import LLMClient, LLMMessage, LLMResult
from app.providers.llm.mock import MockLLMClient


class RecordingLLM(MockLLMClient):
    """把每次调用收到的消息留下来，供断言"这一轮的 system 里到底有什么"。"""

    def __init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    async def complete(self, **kwargs: Any) -> LLMResult:
        self.calls.append(list(kwargs.get("messages") or []))
        return await super().complete(**kwargs)

    def systems(self) -> list[str]:
        """每次调用里 system 消息的内容（按调用顺序）。"""
        return [m.content for call in self.calls for m in call if m.role == "system"]


@pytest.fixture()
def recording_llm(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> RecordingLLM:
    """把 WS 端点用的 LLM 换成会记录的 Mock（WS 内部调 `get_llm()`）。"""
    recorder = RecordingLLM()
    monkeypatch.setattr("app.api.ws.get_llm", lambda: recorder)
    return recorder


def _create_session(client: TestClient, **payload: Any) -> int:
    body: dict[str, Any] = {"mode": "realtime", "max_questions": 4}
    body.update(payload)
    response = client.post("/api/sessions", json=body)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _answer(client: TestClient, session_id: int, text: str) -> dict[str, Any]:
    """连上去、答一轮，返回这一轮收到的事件（按 type 索引）。"""
    events: dict[str, Any] = {}
    with client.websocket_connect(f"/ws/sessions/{session_id}") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "text", "text": text})
        while True:
            event = ws.receive_json()
            events[event["type"]] = event
            if event["type"] == "turn_meta":
                break
    return events


# --------------------------------------------------------------------------- #
# 语言：英文面试在第 2 题起仍要说英文
# --------------------------------------------------------------------------- #
def test_locale_survives_into_later_turns(client: TestClient, recording_llm: RecordingLLM) -> None:
    session_id = _create_session(client, locale="en")
    recording_llm.calls.clear()  # 丢掉开场题那次，只看后续回合

    _answer(client, session_id, "I led the order pipeline rewrite.")

    assert recording_llm.calls, "第二轮应该真的调了模型"
    # 英文指令块必须在**这一轮**的 system 里，而不是只在开场题里出现过
    assert any("English" in system or "英文" in system for system in recording_llm.systems())


def test_zh_session_has_no_english_directive(client: TestClient, recording_llm: RecordingLLM) -> None:
    """中文面试不该被塞进英文指令——说明 locale 是真按值走的，不是无脑附加。"""
    session_id = _create_session(client, locale="zh")
    recording_llm.calls.clear()

    _answer(client, session_id, "我负责订单链路重构。")

    assert not any("Answer in English" in system for system in recording_llm.systems())


# --------------------------------------------------------------------------- #
# 连环面：第 2 题起仍知道"这一轮由谁问"
# --------------------------------------------------------------------------- #
def test_panel_role_reaches_later_turns(client: TestClient, recording_llm: RecordingLLM) -> None:
    session_id = _create_session(
        client,
        panel=[{"style_key": "technical", "rounds": 1}, {"style_key": "hr", "rounds": 3}],
    )
    recording_llm.calls.clear()

    _answer(client, session_id, "我是做后端的。")

    systems = recording_llm.systems()
    assert systems
    # 第 2 轮该由 HR 问：HR 角色的指令必须出现在这一轮
    assert any("HR" in system or "人力" in system or "hr" in system.lower() for system in systems), (
        "连环面第二棒（HR）的指令没有下发到这一轮"
    )


def test_interviewer_meta_is_attached_to_later_turns(client: TestClient) -> None:
    """语音回合的 meta / 事件要带 `interviewer`，否则前端横幅在第 2 题后消失。"""
    session_id = _create_session(
        client,
        panel=[{"style_key": "technical", "rounds": 1}, {"style_key": "hr", "rounds": 3}],
    )

    events = _answer(client, session_id, "我是做后端的。")
    question = events["interviewer_text"]

    assert "interviewer" in question
    interviewer = question["interviewer"]
    assert interviewer["index"] == 1, "第 2 轮应该换成第二位面试官"
    assert interviewer["total"] == 2
    assert interviewer["voice_label"]

    # 落库的回合 meta 也要带上它（历史回看/报告一致）
    detail = client.get(f"/api/sessions/{session_id}").json()
    asked = [turn for turn in detail["turns"] if turn["speaker"] == "interviewer"]
    assert any(turn["meta"].get("interviewer") for turn in asked)


def test_single_interviewer_has_empty_role_meta(client: TestClient) -> None:
    """单面试官面试不该编出一个角色来。"""
    session_id = _create_session(client, style_key="technical")

    events = _answer(client, session_id, "我是做后端的。")
    assert events["interviewer_text"]["interviewer"] == {}


# --------------------------------------------------------------------------- #
# 题库：选了"只用内置题库"，第 2 题起仍按清单问
# --------------------------------------------------------------------------- #
def test_bank_list_survives_into_later_turns(client: TestClient, recording_llm: RecordingLLM) -> None:
    """M13 的题库清单此前整场都没进语音链路，选了"只用内置题"从第 2 题起会变成自由出题。"""
    session_id = _create_session(client, question_mix={"builtin": 3})

    # 先确认这场面试真的抽到了内置题。少了这一步，一旦题库播种有问题，
    # 失败原因会指向"清单没下发"，而真实原因是"压根没抽到题"——两种错法完全不同。
    plan = client.get(f"/api/sessions/{session_id}").json()["plan"]
    assert plan.get("questions"), "这场面试应该抽到内置题（题库播种或抽题坏了）"

    recording_llm.calls.clear()
    _answer(client, session_id, "我做过视觉项目。")

    systems = recording_llm.systems()
    assert systems
    # 清单块由 bank_prompt_block 生成，标题是"【本场面试的题目清单】（按顺序提问）"
    assert any("题目清单" in system for system in systems), "题库清单没有随第二轮下发"


# --------------------------------------------------------------------------- #
# 压力模式：语音链路此前只把压力用在"运行时打断/限时"，prompt 规则拿不到
# --------------------------------------------------------------------------- #
def test_pressure_prompt_reaches_later_turns(client: TestClient, recording_llm: RecordingLLM) -> None:
    session_id = _create_session(client, pressure={"deep_drill": True, "max_depth": 3})
    recording_llm.calls.clear()

    _answer(client, session_id, "我负责标定模块。")

    systems = recording_llm.systems()
    assert any("追问" in system for system in systems), "压力模式的 prompt 规则没有随轮次下发"


# --------------------------------------------------------------------------- #
# 兜底：按轮取块失败时退回固定块，而不是丢掉全部指令
# --------------------------------------------------------------------------- #
class _ExplodingResolver:
    def __call__(self, round_index: int) -> str:
        raise RuntimeError("模拟取块失败")


def test_policy_block_falls_back_when_resolver_raises() -> None:
    """取块函数抛异常时必须退回固定块——一场面试不该因为拼指令失败而失去全部约束。"""
    from app.realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime
    from app.realtime.audio_store import AudioStore
    from app.providers.speech.mock import MockSpeechProvider
    from pathlib import Path
    import tempfile

    runtime = VoiceSessionRuntime(
        session_id=1,
        llm=MockLLMClient(),
        speech=MockSpeechProvider(),
        store=AudioStore(Path(tempfile.mkdtemp()) / "audio", 1, sample_rate=16000),
        sink=_noop_sink,
        config=VoiceRuntimeConfig(
            policy_block="兜底块",
            policy_blocks=_ExplodingResolver(),
        ),
    )
    assert runtime._policy_block_for(1) == "兜底块"


async def _noop_sink(event: dict[str, Any]) -> None:
    return None
