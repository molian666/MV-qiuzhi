"""拟真与反馈（M14）：教练模式、沉默施压、现场突发、简历深挖，以及打断续问。

这五个行为的目标不同，验证也分开看：
1. 开关是否独立生效（规则进 prompt，关着就不出现）；
2. 教练点评是否被解析并随回合落库（前端要拿它展示复盘卡）；
3. 打断续问是否真的把"被打断"传给了下一轮的 prompt（而不是只换个话题重问）；
4. 会话创建是否把这四个开关存进 plan（语音链路要还原成规则）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.analysis.realism import realism_prompt_block
from app.api.sessions import _compose_policy_block
from app.analysis.pressure import PressureConfig
from app.providers.llm.base import LLMResult, LLMMessage
from app.providers.llm.mock import MockLLMClient
from app.providers.speech.mock import MockSpeechProvider
from app.realtime.audio_store import AudioStore
from app.realtime.interviewer import QuestionPlan, build_messages, parse_plan
from app.realtime.session_runtime import VoiceRuntimeConfig, VoiceSessionRuntime

from .audio_fixtures import tone


# --------------------------------------------------------------------------- #
# realism_prompt_block：四个开关独立生效
# --------------------------------------------------------------------------- #
def test_all_off_yields_empty_block() -> None:
    assert realism_prompt_block(coach=False, silence_pressure=False, scene=False, resume_dive=False) == ""
    assert realism_prompt_block() == ""


def test_each_switch_adds_its_own_rule() -> None:
    coach = realism_prompt_block(coach=True)
    assert "教练模式" in coach and "coach" in coach

    silence = realism_prompt_block(silence_pressure=True)
    assert "沉默施压" in silence and "还有要补充的吗" in silence

    scene = realism_prompt_block(scene=True)
    assert "现场突发" in scene and "第 3 题之后" in scene and "kind 固定为 `scene`" in scene

    dive = realism_prompt_block(resume_dive=True)
    assert "简历深挖" in dive and "逐行追问" in dive

    # 每个开关独立：只开一个，另外三个的规则不出现
    assert "沉默施压" not in coach
    assert "现场突发" not in coach
    assert "简历深挖" not in coach
    assert "教练模式" not in silence
    assert "现场突发" not in silence
    assert "简历深挖" not in silence
    assert "教练模式" not in scene
    assert "沉默施压" not in scene
    assert "简历深挖" not in scene
    assert "教练模式" not in dive
    assert "沉默施压" not in dive
    assert "现场突发" not in dive


def test_combined_block_orders_coach_last() -> None:
    block = realism_prompt_block(coach=True, silence_pressure=True, scene=True, resume_dive=True)
    assert all(marker in block for marker in ("教练模式", "沉默施压", "现场突发", "简历深挖"))
    # 教练的 JSON 输出要求必须压在最末尾（越靠后的指令越容易被模型遵守）
    assert block.rindex("教练模式") > block.rindex("简历深挖")
    assert block.rindex("简历深挖") > block.rindex("现场突发")
    assert block.rindex("现场突发") > block.rindex("沉默施压")


def test_english_block() -> None:
    block = realism_prompt_block(coach=True, silence_pressure=True, scene=True, resume_dive=True, locale="en")
    for marker in ("Coaching mode", "Silence pressure", "Scene drill", "Resume deep dive"):
        assert marker in block
    assert "沉默施压" not in block


# --------------------------------------------------------------------------- #
# parse_plan：教练点评解析
# --------------------------------------------------------------------------- #
def _result(text: str) -> LLMResult:
    return LLMResult(text=text, task="interview_turn", model="mock", provider="mock", latency_ms=1)


def test_parse_plan_extracts_coach() -> None:
    plan = parse_plan(_result('{"kind":"new_topic","topic":"项目深挖","question":"你负责哪部分？",'
                               '"coach":{"points":"要讲清你的角色","missed":"没提量化结果","better":"先说结论再展开"}}'))
    assert plan.coach == {"points": "要讲清你的角色", "missed": "没提量化结果", "better": "先说结论再展开"}
    assert plan.question == "你负责哪部分？"
    assert plan.kind == "new_topic"


def test_parse_plan_ignores_malformed_coach() -> None:
    # coach 不是对象 / 空对象 / 全是空白 → 解析成 None，不炸
    assert parse_plan(_result('{"kind":"new_topic","question":"Q","coach":"点评"}')).coach is None
    assert parse_plan(_result('{"kind":"new_topic","question":"Q","coach":{}}')).coach is None
    assert parse_plan(_result('{"kind":"new_topic","question":"Q","coach":{"points":"  "}}')).coach is None
    # 未知键被丢弃，只留三件套
    plan = parse_plan(_result('{"kind":"new_topic","question":"Q","coach":{"points":"P","junk":1,"missed":"M"}}'))
    assert plan.coach == {"points": "P", "missed": "M"}


def test_parse_plan_without_coach() -> None:
    assert parse_plan(_result('{"kind":"followup","question":"继续"}')).coach is None


# --------------------------------------------------------------------------- #
# build_messages：打断续问
# --------------------------------------------------------------------------- #
def test_interrupted_appends_followup_directive() -> None:
    messages = build_messages(turns=[], context="ctx", closing=False, interrupted=True)
    assert messages[-1].role == "user"
    assert "插话打断了候选人" in messages[-1].content
    assert "最后一句" in messages[-1].content


def test_interrupted_flag_is_off_by_default() -> None:
    messages = build_messages(turns=[], context="ctx", closing=False)
    assert all("插话打断" not in m.content for m in messages)


def test_closing_takes_priority_over_interrupted() -> None:
    messages = build_messages(turns=[], context="ctx", closing=True, interrupted=True)
    assert "本轮是面试收尾" in messages[-1].content
    assert "插话打断" not in messages[-1].content


def test_force_scene_appends_hard_directive() -> None:
    messages = build_messages(turns=[], context="ctx", closing=False, force_scene=True)
    assert messages[-1].role == "user"
    assert "这一问必须" in messages[-1].content
    assert "现场突发" in messages[-1].content
    assert "kind 固定为 scene" in messages[-1].content


def test_force_scene_takes_priority_over_interrupted() -> None:
    messages = build_messages(turns=[], context="ctx", closing=False, interrupted=True, force_scene=True)
    assert "现场突发" in messages[-1].content
    assert "插话打断" not in messages[-1].content


def test_closing_takes_priority_over_force_scene() -> None:
    messages = build_messages(turns=[], context="ctx", closing=True, force_scene=True)
    assert "本轮是面试收尾" in messages[-1].content
    assert "现场突发" not in messages[-1].content


def test_force_scene_is_off_by_default() -> None:
    messages = build_messages(turns=[], context="ctx", closing=False)
    assert all("这一问必须" not in m.content for m in messages)


def test_interrupted_takes_priority_over_opening_prompt() -> None:
    # 即使没有旧回合，interrupted 指令也优先于"请自我介绍"引导——
    # 场景只会出现在中段打断，但优先级必须写死，免得模型误开场
    messages = build_messages(turns=[], context="ctx", interrupted=True)
    assert "插话打断了候选人" in messages[-1].content
    assert "现在开始面试" not in messages[-1].content


# --------------------------------------------------------------------------- #
# next_question：interrupted 真正进入发给模型的最后一条消息
# --------------------------------------------------------------------------- #
class RecordingLLM(MockLLMClient):
    """记录每次调用收到的消息，方便断言 prompt 内容。"""

    def __init__(self) -> None:
        self.last_messages: list[LLMMessage] = []

    async def complete(self, **kwargs: Any) -> LLMResult:
        self.last_messages = kwargs.get("messages", [])
        return await super().complete(**kwargs)


def test_next_question_passes_interrupted_flag() -> None:
    from app.realtime.interviewer import Interviewer

    llm = RecordingLLM()
    interviewer = Interviewer(llm)
    _plan, _result = _run_sync(interviewer.next_question(turns=[], max_questions=2, interrupted=True))
    last = llm.last_messages[-1]
    assert "插话打断了候选人" in last.content


def test_next_question_omits_flag_when_not_interrupted() -> None:
    from app.realtime.interviewer import Interviewer

    llm = RecordingLLM()
    interviewer = Interviewer(llm)
    _plan, _result = _run_sync(interviewer.next_question(turns=[], max_questions=2, interrupted=False))
    assert all("插话打断" not in m.content for m in llm.last_messages)


def test_next_question_passes_force_scene() -> None:
    from app.realtime.interviewer import Interviewer

    llm = RecordingLLM()
    interviewer = Interviewer(llm)
    _plan, _result = _run_sync(interviewer.next_question(turns=[], max_questions=4, force_scene=True))
    assert "现场突发" in llm.last_messages[-1].content


def _run_sync(awaitable: Any) -> Any:
    import asyncio

    return asyncio.run(awaitable)


# --------------------------------------------------------------------------- #
# API 层：开关落进 plan、策略块带规则
# --------------------------------------------------------------------------- #
def test_create_session_persists_realism_flags(client: TestClient) -> None:
    created = client.post(
        "/api/sessions",
        json={
            "max_questions": 3,
            "coach": True,
            "silence_pressure": True,
            "scene": True,
            "resume_dive": True,
        },
    ).json()
    plan = created["plan"]
    assert plan["coach"] is True
    assert plan["silence_pressure"] is True
    assert plan["scene"] is True
    assert plan["resume_dive"] is True


def test_create_session_defaults_realism_off(client: TestClient) -> None:
    created = client.post("/api/sessions", json={"max_questions": 2}).json()
    plan = created["plan"]
    assert plan["coach"] is False
    assert plan["silence_pressure"] is False
    assert plan["scene"] is False
    assert plan["resume_dive"] is False


def test_compose_policy_block_includes_realism_when_enabled() -> None:
    block = _compose_policy_block(
        style=None,
        panel=None,
        panel_round=0,
        pressure=PressureConfig(),
        policy=None,
        locale="zh",
        coach=True,
        silence_pressure=True,
        scene=True,
        resume_dive=True,
    )
    assert "拟真与反馈规则" in block
    assert "教练模式" in block and "现场突发" in block
    # 压力没开 → 压力块不出现，但拟真块仍在
    assert "压力模式" not in block


def test_compose_policy_block_skips_realism_when_off() -> None:
    block = _compose_policy_block(
        style=None,
        panel=None,
        panel_round=0,
        pressure=PressureConfig(),
        policy=None,
        locale="zh",
    )
    assert "拟真与反馈规则" not in block


# --------------------------------------------------------------------------- #
# 语音运行时：被打断的标记设置后被消费一次
# --------------------------------------------------------------------------- #
def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


async def test_interrupt_sets_flag_then_next_answer_consumes_it(
    client: TestClient, tmp_path: Path
) -> None:
    created = client.post("/api/sessions", json={"mode": "realtime", "max_questions": 3}).json()
    session_id = int(created["id"])

    events: list[dict[str, Any]] = []
    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    llm = RecordingLLM()
    store = AudioStore(tmp_path / "audio", session_id, sample_rate=16000)
    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=llm,
        speech=MockSpeechProvider(),
        store=store,
        sink=sink,
        config=VoiceRuntimeConfig(
            max_questions=3,
            text_fallback=True,
            silence_ms=300,
            min_answer_ms=200,
            # 说太久就被插话（最小 3s，见 pressure.clamped）
            interrupt_after_ms=3_000,
        ),
    )
    await runtime.start()

    # 一直说、不结尾：音频片尾部触发"面试官插话打断"
    await runtime.handle({"type": "audio_append", "pcm": _b64(tone(4_000))})
    assert any(event["type"] == "pressure" and event["mode"] == "interrupt" for event in events)

    # Mock 语音没有 ASR → 打断后等待文字；此时标志已置位，本轮提交会消费它
    llm.last_messages = []
    await runtime.handle({"type": "text", "text": "第一轮：我说到一半被打断。"})
    last = llm.last_messages[-1]
    assert "插话打断了候选人" in last.content, "被打断后的下一问必须紧贴最后一句续问"

    # 第二轮正常作答：标志已被消费，不再注入打断指令
    llm.last_messages = []
    await runtime.handle({"type": "text", "text": "第二轮：正常作答。"})
    assert all("插话打断" not in m.content for m in llm.last_messages)

    await runtime.close()


async def test_scene_forced_after_third_answer_in_voice(client: TestClient, tmp_path: Path) -> None:
    """语音链路：plan.scene 开启时，第 3 次回答后的下一问必须强制为突发场景题。"""
    created = client.post(
        "/api/sessions", json={"mode": "realtime", "max_questions": 5, "scene": True}
    ).json()
    session_id = int(created["id"])

    events: list[dict[str, Any]] = []
    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    llm = RecordingLLM()
    store = AudioStore(tmp_path / "audio", session_id, sample_rate=16000)
    runtime = VoiceSessionRuntime(
        session_id=session_id,
        llm=llm,
        speech=MockSpeechProvider(),
        store=store,
        sink=sink,
        config=VoiceRuntimeConfig(max_questions=5, text_fallback=True, silence_ms=300, min_answer_ms=200),
    )
    await runtime.start()

    for index, answer in enumerate(
        ["第一轮自我介绍。", "第二轮项目细节。", "第三轮技术追问。", "第四轮继续。"]
    ):
        llm.last_messages = []
        await runtime.handle({"type": "text", "text": answer})
        last = llm.last_messages[-1].content if llm.last_messages else ""
        if index == 2:
            # 第 3 次回答后：强制突发场景
            assert "现场突发" in last and "这一问必须" in last, f"第 3 轮后应强制突发题，实际：{last[:80]}"
        elif index == 3:
            # 第 4 轮恢复常态（不再强制）
            assert "这一问必须" not in last
        else:
            assert "现场突发" not in last

    await runtime.close()
