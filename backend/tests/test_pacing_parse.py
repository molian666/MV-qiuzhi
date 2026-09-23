"""对话文稿解析与问题切分的测试。

这一层决定了"画像到底在看什么"：切错了问题，后面所有节奏特征都是错的。
"""

from __future__ import annotations

from app.analysis.pacing import parse_transcript, split_questions

DIALOG = """[面试官] 先做个自我介绍吧，重点讲一下最近的项目。
[我] 我是 5 年后端，最近在做订单系统重构。
[面试官] 这个项目里你具体负责哪一部分？
[我] 我负责下单链路的重构。
[面试官] 那当时有没有考虑过别的方案？
[我] 有，考虑过消息队列。
"""


def test_parses_speaker_labels_and_order() -> None:
    segments = parse_transcript(DIALOG)
    assert [segment.speaker for segment in segments] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
    ]
    assert segments[1].text.startswith("我是 5 年后端")


def test_supports_timestamp_and_english_colon_form() -> None:
    text = "00:12 面试官：你简历里这个项目讲一下\n00:20 我：这个项目是订单系统\nQ: 为什么离职\nA: 想换方向"
    segments = parse_transcript(text)
    assert segments[0].start_ms == 12_000
    assert segments[1].start_ms == 20_000
    assert [segment.speaker for segment in segments] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
    ]


def test_estimates_timeline_when_missing() -> None:
    segments = parse_transcript("面试官：你好\n我：你好")
    assert segments[0].start_ms == 0
    assert segments[0].end_ms is not None and segments[0].end_ms > 0
    # 没有时间戳时，时间轴首尾相接（前一段结束即后一段开始）
    assert segments[1].start_ms == segments[0].end_ms


def test_wait_time_comes_from_real_gap() -> None:
    """带时间戳时，发言时长不能被硬拉到下一段开始，否则等待时长永远算成 0。"""
    text = "00:00 面试官：讲讲你的项目\n00:10 我：我做的是订单系统"
    units = split_questions(parse_transcript(text))
    assert units[0].wait_ms is not None
    assert units[0].wait_ms > 3000  # 约 8.5 秒的等待（10 秒减去面试官发言时长）


def test_splits_multiple_questions_in_one_speech() -> None:
    segments = parse_transcript("面试官：你负责哪部分？整体方案怎么设计的？")
    units = split_questions(segments)
    assert len(units) == 2
    assert units[0].kind == "opening"
    assert units[1].kind == "new_topic"


def test_followup_is_attached_to_previous_question() -> None:
    text = "面试官：讲讲你的项目\n我：我做的是订单系统\n面试官：那有没有考虑别的方案？\n我：有"
    units = split_questions(parse_transcript(text))
    assert len(units) == 1
    assert units[0].followups == ["那有没有考虑别的方案？"]
    assert units[0].depth == 1


def test_new_topic_after_answer_is_not_followup() -> None:
    text = "面试官：讲讲你的项目\n我：我做的是订单系统\n面试官：你平时的技术栈是什么？\n我：Python"
    units = split_questions(parse_transcript(text))
    assert len(units) == 2
    assert units[1].followups == []


def test_closing_question_is_marked() -> None:
    text = "面试官：讲讲项目\n我：好\n面试官：你有什么想问我的吗？"
    units = split_questions(parse_transcript(text))
    assert units[-1].kind == "closing"


def test_answer_duration_is_accumulated() -> None:
    text = "面试官：讲讲项目\n我：第一段\n我：第二段"
    units = split_questions(parse_transcript(text))
    assert len(units) == 1
    assert units[0].answer_ms is not None
    assert units[0].answer_ms > 0


def test_unknown_speaker_lines_are_ignored_for_units() -> None:
    """既没有说话人标记、也不像问题的行不该凭空产生问题单元。"""
    units = split_questions(parse_transcript("这是一段没有说话人标记的说明文字"))
    assert units == []
