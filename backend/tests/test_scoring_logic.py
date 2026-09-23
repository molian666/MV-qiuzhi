"""评分纯逻辑的测试：问答对折叠、prompt 构造、模型输出规范化。

规范化是这一层的重点——模型输出不可信（缺字段、分数越界、引用不存在的回合都可能出现），
而报告页、趋势图、后续训练都建立在这份数据上。
"""

from __future__ import annotations

from typing import Any

from app.analysis.rubric import dimension_keys
from app.analysis.scoring import (
    build_exchanges,
    build_scoring_messages,
    normalize_report,
)
from app.models.session import SessionTurn


def _turn(
    idx: int,
    speaker: str,
    text: str,
    *,
    kind: str = "",
    is_followup: bool = False,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> SessionTurn:
    """未绑定的 SessionTurn（不需要数据库）。id 用 idx+1 便于断言。"""
    return SessionTurn(
        id=idx + 1,
        session_id=1,
        idx=idx,
        speaker=speaker,
        text=text,
        kind=kind,
        is_followup=is_followup,
        start_ms=start_ms,
        end_ms=end_ms,
    )


# --------------------------------------------------------------------------- #
# build_exchanges
# --------------------------------------------------------------------------- #
def test_exchanges_pair_questions_with_answers() -> None:
    turns = [
        _turn(0, "interviewer", "先做个自我介绍", kind="opening"),
        _turn(1, "candidate", "我是后端", start_ms=0, end_ms=1200),
        _turn(2, "interviewer", "项目难点是什么", kind="new_topic"),
        _turn(3, "candidate", "是并发写", start_ms=1200, end_ms=4000),
    ]

    exchanges = build_exchanges(turns)

    assert len(exchanges) == 2
    assert exchanges[0].question_turn_id == 1
    assert exchanges[0].answer_turn_id == 2
    assert exchanges[0].answer_ms == 1200
    assert exchanges[1].question == "项目难点是什么"
    assert exchanges[1].answer == "是并发写"


def test_closing_turn_is_not_a_question() -> None:
    turns = [
        _turn(0, "interviewer", "开场", kind="opening"),
        _turn(1, "candidate", "回答"),
        _turn(2, "interviewer", "我的问题问完了", kind="closing"),
    ]
    exchanges = build_exchanges(turns)
    assert len(exchanges) == 1
    assert all("问完了" not in exchange.question for exchange in exchanges)


def test_answer_without_question_is_kept() -> None:
    """没有对应问题的回答不能静默丢掉（否则用户少看到一次作答的分析）。"""
    turns = [_turn(0, "candidate", "我先补充一句")]
    exchanges = build_exchanges(turns)
    assert len(exchanges) == 1
    assert exchanges[0].question_kind == "orphan"
    assert exchanges[0].answer == "我先补充一句"


def test_followup_flag_is_preserved() -> None:
    turns = [
        _turn(0, "interviewer", "项目难点？", kind="followup", is_followup=True),
        _turn(1, "candidate", "并发写"),
    ]
    exchanges = build_exchanges(turns)
    assert exchanges[0].is_followup is True


# --------------------------------------------------------------------------- #
# build_scoring_messages
# --------------------------------------------------------------------------- #
def test_scoring_messages_carry_record_and_rubric() -> None:
    exchanges = build_exchanges(
        [
            _turn(0, "interviewer", "自我介绍", kind="opening"),
            _turn(1, "candidate", "我是后端"),
        ]
    )
    messages = build_scoring_messages(
        target_label="某公司 · 后端",
        resume_text="5 年后端",
        duration_ms=60_000,
        max_questions=3,
        exchanges=exchanges,
    )

    assert messages[0].role == "system"
    assert "JSON" in messages[0].content
    user = messages[1].content
    assert "【本场面试记录 JSON】" in user
    assert "【记录结束】" in user
    assert "某公司 · 后端" in user
    assert "我是后端" in user
    # rubric 说明必须在同一个 prompt 里，否则模型不知道按什么打分
    assert "切题度" in user
    assert "主动性" in user


# --------------------------------------------------------------------------- #
# normalize_report
# --------------------------------------------------------------------------- #
def test_normalize_fills_missing_dimensions_with_default() -> None:
    report = normalize_report({"dimensions": {"depth": {"score": 5}}}, valid_turn_ids={1})
    assert len(report.dimensions) == len(dimension_keys())
    by_key = {dimension.key: dimension for dimension in report.dimensions}
    assert by_key["depth"].score == 5
    assert by_key["structure"].score == 3
    assert "未给出" in by_key["structure"].comment


def test_normalize_clamps_and_rounds_scores() -> None:
    raw: dict[str, Any] = {
        "dimensions": {
            "relevance": {"score": 9},
            "structure": {"score": -2},
            "depth": {"score": 4.6},
        }
    }
    report = normalize_report(raw, valid_turn_ids=set())
    by_key = {dimension.key: dimension.score for dimension in report.dimensions}
    assert by_key["relevance"] == 5
    assert by_key["structure"] == 1
    assert by_key["depth"] == 5


def test_normalize_overall_defaults_to_dimension_average() -> None:
    raw = {"dimensions": {key: {"score": 4} for key in dimension_keys()}}
    report = normalize_report(raw, valid_turn_ids=set())
    assert report.overall == 80  # 4/5 × 100

    explicit = normalize_report({"overall": 150, "dimensions": raw["dimensions"]}, valid_turn_ids=set())
    assert explicit.overall == 100  # clamp 到 100


def test_normalize_filters_bogus_evidence_ids() -> None:
    raw = {
        "dimensions": {"depth": {"score": 4, "evidence_turn_ids": [1, 999, "x", 2, 1]}},
        "items": [{"dimension": "depth", "evidence_turn_ids": [2, 777]}],
    }
    report = normalize_report(raw, valid_turn_ids={1, 2})
    by_key = {dimension.key: dimension for dimension in report.dimensions}
    assert by_key["depth"].evidence_turn_ids == [1, 2]  # 越界与非法值被剔除，且去重
    assert report.items[0].evidence_turn_ids == [2]


def test_normalize_sorts_items_by_severity_and_caps_length() -> None:
    raw = {
        "items": [
            {"severity": "low", "dimension": "depth", "problem": "l"},
            {"severity": "high", "dimension": "structure", "problem": "h"},
            {"severity": "medium", "dimension": "delivery", "problem": "m"},
        ]
    }
    report = normalize_report(raw, valid_turn_ids=set())
    assert [item.severity for item in report.items] == ["high", "medium", "low"]


def test_normalize_reassigns_unknown_dimension_to_weakest() -> None:
    raw = {
        "dimensions": {"depth": {"score": 1}},
        "items": [{"severity": "high", "dimension": "自创维度", "problem": "跑题了"}],
    }
    report = normalize_report(raw, valid_turn_ids=set())
    assert report.items[0].dimension == "depth"  # 挂到最薄弱的维度，而不是丢掉这条建议
    assert report.items[0].dimension_label == "技术深度与正确性"


def test_normalize_handles_garbage_input() -> None:
    """模型返回垃圾（非 dict、空、字符串）时不能抛异常，报告页必须还能显示。"""
    for garbage in (None, "not a dict", 42, [], {}, {"dimensions": "oops"}):
        report = normalize_report(garbage, valid_turn_ids=set())
        assert len(report.dimensions) == len(dimension_keys())
        assert 0 <= report.overall <= 100
        assert report.summary
        assert report.items  # 兜底至少给一条可执行提示


def test_weakest_dimension_helper() -> None:
    report = normalize_report(
        {"dimensions": {"depth": {"score": 1}, "structure": {"score": 5}}},
        valid_turn_ids=set(),
    )
    assert report.weakest is not None
    assert report.weakest.key == "depth"


def test_dimensions_payload_shape() -> None:
    """落库形状是 {key: {...}}：M9/M11 加维度时不会打乱既有读取逻辑。"""
    report = normalize_report({"dimensions": {"depth": {"score": 4}}}, valid_turn_ids=set())
    payload = report.dimensions_payload()
    assert set(payload) == set(dimension_keys())
    assert payload["depth"]["score"] == 4
    assert payload["depth"]["label"] == "技术深度与正确性"
