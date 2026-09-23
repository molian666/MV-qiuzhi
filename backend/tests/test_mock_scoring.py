"""Mock 启发式评分的测试。

重点是"分数与证据必须随输入变化"——如果 Mock 只是返回固定数字，
那么 M3 的整条链路（落库、报告页、趋势）在没有 Key 的情况下等于没被验证过。
"""

from __future__ import annotations

import json
from typing import Any

from app.providers.llm.base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage
from app.providers.llm.mock_scoring import build_mock_scoring_json, extract_record

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _messages(payload: dict[str, Any]) -> list[LLMMessage]:
    return [
        LLMMessage(
            role="user",
            content=f"{RECORD_MARKER_START}\n{json.dumps(payload, ensure_ascii=False)}\n{RECORD_MARKER_END}",
        )
    ]


def _report(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(build_mock_scoring_json(_messages(payload)))


def test_extract_record_reads_marked_block() -> None:
    payload = {"exchanges": [{"answer": "x"}]}
    assert extract_record(_messages(payload)) == payload
    assert extract_record([LLMMessage("user", "没有标记的文本")]) == {}


def test_numbers_drive_quantification_score() -> None:
    weak = _report({"exchanges": [{"answer_turn_id": 1, "answer": "做了些优化，效果还行"}]})
    strong = _report(
        {
            "exchanges": [
                {"answer_turn_id": 1, "answer": "QPS 从 800 提到 4200，P99 从 380ms 降到 90ms，成本增加 5%"}
            ]
        }
    )
    assert strong["dimensions"]["quantification"]["score"] > weak["dimensions"]["quantification"]["score"]


def test_structure_words_drive_structure_score() -> None:
    weak = _report({"exchanges": [{"answer_turn_id": 1, "answer": "就是把那块改了改"}]})
    strong = _report(
        {"exchanges": [{"answer_turn_id": 1, "answer": "首先结论是 X，其次做法是 Y，最后结果是 Z。"}]}
    )
    assert strong["dimensions"]["structure"]["score"] > weak["dimensions"]["structure"]["score"]


def test_evidence_points_at_the_relevant_answer() -> None:
    """量化问题该引用"最缺数字"的那条回答，而不是无脑取第一条。"""
    report = _report(
        {
            "exchanges": [
                {"answer_turn_id": 11, "answer": "QPS 从 800 提到 4200，P99 降到 90ms，成本增加 5%"},
                {"answer_turn_id": 22, "answer": "就是做了些事情吧"},
            ]
        }
    )
    assert report["dimensions"]["quantification"]["evidence_turn_ids"][:1] == [22]


def test_text_mode_time_control_is_neutral_and_explained() -> None:
    """文字作答的"时长"只是打字时间：不据此扣分，并说明原因。"""
    report = _report({"exchanges": [{"answer_turn_id": 1, "answer": "简短回答", "answer_ms": 1200}]})
    assert report["dimensions"]["time_control"]["score"] == 3
    assert "打字时间" in report["dimensions"]["time_control"]["comment"]


def test_items_are_sorted_by_severity() -> None:
    report = _report({"exchanges": [{"answer_turn_id": 1, "answer": "嗯"}]})
    severities = [item["severity"] for item in report["items"]]
    assert severities == sorted(severities, key=lambda value: SEVERITY_ORDER[value])


def test_text_mode_skips_time_control_advice() -> None:
    """无法评估的维度就不该给建议，否则是误导。"""
    report = _report({"exchanges": [{"answer_turn_id": 1, "answer": "嗯", "answer_ms": 1200}]})
    assert all(item["dimension"] != "time_control" for item in report["items"])
    assert report["items"], "跳过一项后仍应有改进项"


def test_empty_record_still_returns_valid_report() -> None:
    report = _report({})
    assert "overall" in report
    assert report["dimensions"]
    assert report["summary"]
    assert report["items"]
