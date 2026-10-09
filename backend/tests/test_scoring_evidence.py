"""评分的两处"有据可依"改进。

审计发现的两个问题都属于同一类：**模型评分没有尺子**。

1. 题库里的**期望要点**根本没进评分 payload——"他答到要点了没有"没有任何依据，
   模型只能凭印象打分，同一份回答两次可能差很多。
2. 候选人在收尾阶段的**提问**被当成"上一个问题的回答"，主动性维度拿不到证据。

这个文件把这两件事钉住。
"""

from __future__ import annotations

import json
from typing import Any

from app.analysis.scoring import (
    build_exchanges,
    build_scoring_messages,
    normalize_report,
)
from app.models.session import SessionTurn


def _turn(idx: int, speaker: str, text: str, kind: str = "", **kwargs: Any) -> SessionTurn:
    return SessionTurn(
        id=idx + 1,
        session_id=1,
        idx=idx,
        speaker=speaker,
        text=text,
        kind=kind or ("answer" if speaker == "candidate" else "new_topic"),
        **kwargs,
    )


def _payload_of(messages: list[Any]) -> dict[str, Any]:
    """从 user 消息里把那段 JSON 抠出来（与生产用的是同一套包裹标记）。"""
    user = next(message for message in messages if message.role == "user")
    start = user.content.index("{")
    end = user.content.rindex("}") + 1
    return json.loads(user.content[start:end])


# --------------------------------------------------------------------------- #
# 期望要点要进 payload
# --------------------------------------------------------------------------- #
def test_expected_points_reach_the_scoring_payload() -> None:
    messages = build_scoring_messages(
        target_label="某公司 · 视觉工程师",
        resume_text="做过标定",
        duration_ms=1000,
        max_questions=2,
        exchanges=[],
        expected_points=[
            {"text": "九点标定怎么做？", "points": ["用九组对应点", "注意回测点不要复用"]},
            {"text": "亚像素精度能到多少？", "points": ["经验上 1/5~1/10 像素当量"]},
        ],
    )
    payload = _payload_of(messages)

    assert len(payload["expected_points"]) == 2
    assert payload["expected_points"][0]["question"] == "九点标定怎么做？"
    assert "用九组对应点" in payload["expected_points"][0]["points"]


def test_scoring_prompt_tells_the_model_to_use_the_points() -> None:
    """光把要点塞进去不够——还要明确要求以它为判据，否则模型可能忽略它。"""
    messages = build_scoring_messages(
        target_label="",
        resume_text="",
        duration_ms=None,
        max_questions=1,
        exchanges=[],
        expected_points=[{"text": "Q", "points": ["P"]}],
    )
    user = next(message for message in messages if message.role == "user")

    assert "expected_points" in user.content
    assert "要点覆盖情况" in user.content
    # 明确禁止"讲得流畅就给高分"——那正是"过度宽容"的表现
    assert "不要" in user.content


def test_no_expected_points_means_no_hint() -> None:
    """自由出题的场次没有要点，不该硬塞一段用不上的指令进去。"""
    messages = build_scoring_messages(
        target_label="",
        resume_text="",
        duration_ms=None,
        max_questions=1,
        exchanges=[],
        expected_points=[],
    )
    payload = _payload_of(messages)
    user = next(message for message in messages if message.role == "user")

    assert "expected_points" not in payload
    assert "要点覆盖情况" not in user.content


def test_malformed_expected_points_are_skipped() -> None:
    """配置坏了不该让评分整体失败，只跳过坏的那些。"""
    messages = build_scoring_messages(
        target_label="",
        resume_text="",
        duration_ms=None,
        max_questions=1,
        exchanges=[],
        expected_points=[{"text": "", "points": ["没有题面"]}, {"points": ["x"]}, {"text": "好题", "points": []}],
    )
    payload = _payload_of(messages)

    assert [item["question"] for item in payload["expected_points"]] == ["好题"]


# --------------------------------------------------------------------------- #
# 候选人的提问要单独成条
# --------------------------------------------------------------------------- #
def test_candidate_question_becomes_its_own_exchange() -> None:
    """以前它会被算成"上一个问题的答案"，主动性维度拿不到证据。"""
    turns = [
        _turn(0, "interviewer", "介绍一下你的项目"),
        _turn(1, "candidate", "我做过标定"),
        _turn(2, "interviewer", "你有什么想问我的吗", kind="closing"),
        _turn(3, "candidate", "请问团队主要做什么方向？", kind="question"),
    ]

    exchanges = build_exchanges(turns)

    # 收尾语不算题，所以只有第一组问答 + 一条"候选人提问"
    assert len(exchanges) == 2
    assert exchanges[0].question_kind == "new_topic"
    assert exchanges[0].answer == "我做过标定"

    asked = exchanges[1]
    assert asked.question_kind == "candidate_question"
    assert asked.answer == "请问团队主要做什么方向？"
    # 关键：它没有被挂到"上一个问题"上
    assert asked.question == "（候选人提问）"


def test_candidate_question_does_not_overwrite_a_pending_answer() -> None:
    """收尾阶段如果前面还有悬着的问题，提问不该被当成它的答案。"""
    turns = [
        _turn(0, "interviewer", "问题一"),
        _turn(1, "candidate", "提问但没有回答", kind="question"),
    ]

    exchanges = build_exchanges(turns)

    assert len(exchanges) == 2
    assert exchanges[0].answer == "", "第一组的答案不该被提问填上"
    assert exchanges[1].question_kind == "candidate_question"


def test_orphan_answer_still_recorded() -> None:
    """没有对应问题的回答仍然单独成条——内容不能被静默丢掉。"""
    turns = [_turn(0, "candidate", "孤立的回答")]

    exchanges = build_exchanges(turns)

    assert len(exchanges) == 1
    assert exchanges[0].question_kind == "orphan"
    assert exchanges[0].answer == "孤立的回答"


def test_closing_answer_is_not_treated_as_a_question() -> None:
    """面试官最后那句回答（kind=closing）不该被当成一道题。"""
    turns = [
        _turn(0, "interviewer", "你有什么想问的", kind="closing"),
        _turn(1, "candidate", "团队做什么方向", kind="question"),
        _turn(2, "interviewer", "我们主要做工业检测", kind="closing"),
    ]

    exchanges = build_exchanges(turns)

    assert [item.question_kind for item in exchanges] == ["candidate_question"]


# --------------------------------------------------------------------------- #
# 与总分口径的关系
# --------------------------------------------------------------------------- #
def test_report_with_only_the_question_scored_is_not_inflated() -> None:
    """只有部分维度有分时，总分只按**评过的**算，不被缺项拉向中间。"""
    report = normalize_report(
        {"dimensions": {"depth": {"score": 2}, "structure": {"score": 5}}},
        valid_turn_ids=set(),
    )
    # 两个都评过 → 均值 (2+5)/2 = 3.5 → 70
    assert report.overall == 70


def test_report_ignores_dimensions_the_model_omitted_when_averaging() -> None:
    report = normalize_report({"dimensions": {"depth": {"score": 1}}}, valid_turn_ids=set())
    scored = [item for item in report.dimensions if item.scored]
    unscored = [item for item in report.dimensions if not item.scored]

    assert len(scored) == 1
    assert unscored, "其余维度应当是未评分"
    # 1/5 × 100 = 20，而不是被六个"3 分占位"拉到 60
    assert report.overall == 20
