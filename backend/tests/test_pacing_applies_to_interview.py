"""画像必须**真的作用于模拟面试**，而不只是"存下来给人看"。

这是本功能的验收标准。以前只测到"plan 里有 pacing 字段"——
那只证明数据被存下来了，不证明面试官会照着面。
这里一路断言到**发给面试官模型的 system 消息**：

    真实对话 → 学出画像 → 建模拟面试 → 拼 system 消息 → 内含真实原题与题型配比

对应三条要求：
1. 学到的是**这个面试官问过的问题**（原题要进 prompt，不是只给个比例）；
2. 学到的是**他的节奏**（追问深度、等待时长、语速写进行为指令）；
3. 两个不同的面试官要问出**可区分**的东西（否则画像等于没生效）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.analysis.pacing import build_policy, policy_prompt_block
from app.models.pacing import PacingSample
from app.realtime.interviewer import build_context, build_messages
from tests.fixtures_real_transcript import RAW_TRANSCRIPT


def _profile_from(client: TestClient, text: str, name: str) -> dict:
    recording = client.post("/api/recordings", json={"title": name, "raw_text": text}).json()
    response = client.post(
        "/api/interviewer-profiles",
        json={"recording_ids": [recording["id"]], "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _prompt_parts(client: TestClient, session_id: int) -> tuple[str, str]:
    """返回 (完整 system 消息, 画像那一段)。

    分开返回是因为基础 prompt 里本来就有 `new_topic` 这类词
    （JSON 协议里的 `kind` 取值），拿整个 system 消息去断言"没有 new_topic"会误报。
    要检查画像有没有泄漏英文结构键，必须只看画像那一段。

    **注意 samples 必须传真实的**：画像的真实题型配比与真实原题都在样本里
    （`params` 只存了前 8 道题）。漏传会静默降级成"只有结构比例"的旧行为——
    这正是这个功能最容易退化的地方，所以这里刻意不复刻那种错误调用。
    """
    session = client.get(f"/api/sessions/{session_id}").json()
    policy_block = ""
    profile_id = session.get("interviewer_profile_id")
    if profile_id:
        detail = client.get(f"/api/interviewer-profiles/{profile_id}").json()
        samples = [
            PacingSample(
                question_type=item["question_type"],
                question=item["question"],
            )
            for item in detail["samples"]
        ]
        policy = build_policy(
            detail["params"],
            profile_id=profile_id,
            profile_name=detail["name"],
            samples=samples,
        )
        policy_block = policy_prompt_block(policy)
    context = build_context(None, None, max_questions=5, style="技术面")
    if policy_block:
        context = f"{context}\n\n{policy_block}"
    system = build_messages(turns=[], context=context, closing=False)[0].content
    return system, policy_block


def test_real_questions_are_injected_into_interviewer_prompt(client: TestClient) -> None:
    """**最关键的一条**：面试官真实问过的题要进 prompt。

    以前只注入聚合比例（"技术细节 50%"），模型只能泛泛地问；
    把原题摆出来，它才会问出这个人真正关心的点。
    """
    profile = _profile_from(client, RAW_TRANSCRIPT, "华海达面试官")
    session = client.post(
        "/api/sessions",
        json={"interviewer_profile_id": profile["id"], "max_questions": 5},
    ).json()

    policy = session["plan"]["pacing"]
    assert policy["sample_questions"], "画像里应当带上真实原题"
    joined = " ".join(policy["sample_questions"])
    assert "是做视觉工程师是吧" in joined

    system, _block = _prompt_parts(client, session["id"])
    assert "这个面试官在真实面试里问过" in system
    assert "是做视觉工程师是吧" in system, "真实原题必须真的进入 system 消息"
    # 题目要作为**素材**给模型，同时明确别照念（否则就成了背题）
    assert "不要原样照念" in system


def test_type_mix_uses_question_types_not_structure(client: TestClient) -> None:
    """题型配比要用真实题型，而不是"新话题/追问"这类结构比例。

    结构比例（new_topic 86%）对"问什么内容"几乎没有指导意义；
    而且它的 key 不在中文标签表里，曾经会以英文 `new_topic` 直接漏进 prompt。
    """
    profile = _profile_from(client, RAW_TRANSCRIPT, "题型配比")
    session = client.post(
        "/api/sessions",
        json={"interviewer_profile_id": profile["id"], "max_questions": 5},
    ).json()

    type_mix = session["plan"]["pacing"]["type_mix"]
    assert "技术细节" in type_mix, f"应当有真实题型：{type_mix}"
    assert not any(key in {"new_topic", "followup", "opening"} for key in type_mix), (
        "结构键不该混进题型配比——它和题型不是一个维度，混着显示会自相矛盾"
    )

    _system, block = _prompt_parts(client, session["id"])
    assert "技术细节" in block, f"画像那一段应当有真实题型：{block[:200]}"
    assert "new_topic" not in block, "英文结构键不该漏进画像那一段"


def test_pacing_parameters_reach_the_prompt(client: TestClient) -> None:
    """节奏参数（追问深度 / 等待时长 / 语速）要写进面试官行为指令。"""
    profile = _profile_from(client, RAW_TRANSCRIPT, "节奏参数")
    session = client.post(
        "/api/sessions",
        json={"interviewer_profile_id": profile["id"], "max_questions": 5},
    ).json()

    policy = session["plan"]["pacing"]
    system, _block = _prompt_parts(client, session["id"])
    assert f"最多追问 {policy['max_followup_depth']} 层" in system
    assert f"{policy['interviewer_wpm']} 字/分钟" in system
    # 等待时长不进 prompt（它是运行时 VAD 参数），但必须落进 plan 供语音链路用
    assert policy["silence_ms"] > 0


def test_opening_line_is_chronological_not_representative(client: TestClient) -> None:
    """开场话术要取**时间上最早**的那道题。

    代表性原题是按题型轮转挑的，可能落在面试中段；拿它当开场语就完全不对了。
    """
    profile = _profile_from(client, RAW_TRANSCRIPT, "开场话术")
    session = client.post(
        "/api/sessions",
        json={"interviewer_profile_id": profile["id"], "max_questions": 5},
    ).json()

    policy = session["plan"]["pacing"]
    assert policy["opening_line"] == "是做视觉工程师是吧？", (
        f"开场应是第一道题，实际取了：{policy['opening_line']}"
    )
    system, _block = _prompt_parts(client, session["id"])
    assert "开场用类似这样的话术" in system


def test_two_interviewers_produce_distinguishable_prompts(client: TestClient) -> None:
    """两个风格不同的面试官 → 面试官拿到的指令必须可区分。

    这是"画像真的生效"最直接的证据：如果两份 prompt 一样，
    那画像就只是给人看的装饰。
    """
    pressing = {
        "pace": {"wait_ms": 2600, "interviewer_wpm": 300},
        "followup": {"max_depth": 4, "trigger": ["回答缺少量化数据"]},
        "mix": {"tech": 0.8, "project_deep": 0.2},
        "stress": "high",
        "style_tags": ["紧盯细节连续追问"],
        "patterns": {"opening": "要求自我介绍", "closing": "面试官直接收尾"},
    }
    gentle = {
        "pace": {"wait_ms": 900, "interviewer_wpm": 200},
        "followup": {"max_depth": 1, "trigger": ["回答缺少细节"]},
        "mix": {"behavior": 0.7, "general": 0.3},
        "stress": "low",
        "style_tags": ["给足思考时间"],
        "patterns": {"opening": "寒暄后进入", "closing": "有反问/薪资环节"},
    }

    block_a = policy_prompt_block(build_policy(pressing, profile_name="甲面试官"))
    block_b = policy_prompt_block(build_policy(gentle, profile_name="乙面试官"))

    assert block_a != block_b
    assert "最多追问 4 层" in block_a and "最多追问 1 层" in block_b
    assert "压力水平：high" in block_a and "压力水平：low" in block_b
    assert "300 字/分钟" in block_a and "200 字/分钟" in block_b
    assert "紧盯细节连续追问" in block_a and "给足思考时间" in block_b


def test_profile_without_samples_still_works(client: TestClient) -> None:
    """旧画像（只有 params、没有节奏样本）不能变成空画像。

    真实场景：这个版本之前学出来的画像，样本表里可能没有对应行。
    那种情况下至少要把 params 里存的题型配比与开场方式带上。
    """
    legacy = {
        "pace": {"wait_ms": 1500, "interviewer_wpm": 260},
        "followup": {"max_depth": 2, "trigger": ["没有讲到取舍或边界"]},
        "mix": {"tech": 0.6, "behavior": 0.4},
        "stress": "medium",
        "style_tags": ["标准技术面"],
        "patterns": {"opening": "要求自我介绍", "closing": "面试官直接收尾"},
    }
    block = policy_prompt_block(build_policy(legacy, profile_name="老画像"))

    assert "技术细节" in block, "旧数据的 mix 要能兜底成题型配比"
    assert "60%" in block
    assert "最多追问 2 层" in block
    assert "开场方式：要求自我介绍" in block
