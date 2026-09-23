"""节奏特征量化与运行时策略的测试。

"画像在模拟中生效"这条验收，落到代码上就是 `build_policy` + `policy_prompt_block`——
这两个函数的输出必须能反映画像差异，否则模拟时什么都没变。
"""

from __future__ import annotations

import pytest

from app.analysis.pacing import (
    build_params,
    build_policy,
    build_style_tags,
    compute_features,
    policy_prompt_block,
    policy_summary,
    split_questions,
)
from app.providers.asr.base import SegmentDraft


def _seg(idx: int, speaker: str, text: str, start: int, end: int) -> SegmentDraft:
    return SegmentDraft(idx=idx, speaker=speaker, text=text, start_ms=start, end_ms=end)


def _interview() -> list[SegmentDraft]:
    return [
        _seg(0, "interviewer", "先做个自我介绍吧，重点讲一下你最近的项目", 0, 3000),
        _seg(1, "candidate", "我是 5 年后端工程师，最近在做订单系统重构", 5000, 11000),
        _seg(2, "interviewer", "这个项目里你具体负责哪一部分？", 11000, 13000),
        _seg(3, "candidate", "我负责下单链路的重构", 15000, 20000),
    ]


def test_features_are_quantified() -> None:
    segments = _interview()
    units = split_questions(segments)
    features = compute_features(units, segments)

    assert features.question_count == 2
    assert features.wait_ms == 2000  # 两题都等了 2 秒
    assert features.interviewer_wpm and features.interviewer_wpm > 0
    assert features.candidate_wpm and features.candidate_wpm > 0
    assert features.speaking_ratio is not None
    assert features.opening_pattern == "要求自我介绍"
    assert features.sources["wait_ms"] == "measured"


def test_topic_distribution_uses_labels_when_available() -> None:
    segments = _interview()
    units = split_questions(segments)
    labels = {0: {"type": "self_intro"}, 1: {"type": "project_deep"}}

    features = compute_features(units, segments, labels=labels)
    assert set(features.topic_distribution) == {"self_intro", "project_deep"}
    assert features.topic_distribution["self_intro"] == 0.5


def test_stress_level_reflects_followups_and_interruptions() -> None:
    segments = _interview()
    units = split_questions(segments)
    calm = compute_features(units, segments)
    assert calm.stress_level in {"low", "medium"}

    # 同一批问题，人为加上多层追问 → 压力水平应该升高
    units[0].followups = ["具体呢？", "还有呢？"]
    units[1].followups = ["再讲讲？", "为什么？"]
    pressed = compute_features(units, segments)
    assert pressed.stress_level == "high"
    assert pressed.followup_depth >= 1.5


def test_interrupt_rate_is_measured_from_overlap() -> None:
    segments = [
        _seg(0, "interviewer", "讲讲你的项目", 0, 2000),
        _seg(1, "candidate", "我做的是订单系统", 2000, 8000),
        _seg(2, "interviewer", "等一下，你说的订单系统是哪个？", 6000, 8000),  # 候选人还在说
    ]
    units = split_questions(segments)
    features = compute_features(units, segments)
    assert features.interrupt_rate > 0


def test_followups_count_towards_topic_distribution() -> None:
    """爱追问的面试官，题型分布里必须体现出来。

    否则一个连追四层的人，画像会显示成"只问了一题"——完全看不出风格。
    """
    segments = _interview()
    units = split_questions(segments)
    units[0].followups = ["具体呢？", "还有呢？"]
    units[1].followups = ["再讲讲？", "为什么？", "所以呢？"]

    features = compute_features(units, segments)
    assert features.topic_distribution.get("followup", 0) > 0
    # 追问共 5 次 + 2 个问题 = 7 个样本，追问占比应该可观
    assert features.topic_distribution["followup"] >= 0.5


def test_style_tags_describe_the_interviewer() -> None:
    segments = _interview()
    units = split_questions(segments)
    # 两题都带两层追问 → 平均追问深度 2.0，应该命中"紧盯细节连续追问"
    units[0].followups = ["具体呢？", "还有呢？"]
    units[1].followups = ["再讲讲？", "为什么？"]
    features = compute_features(units, segments)

    tags = build_style_tags(features)
    assert any("追问" in tag for tag in tags)
    assert any("自我介绍" in tag for tag in tags)


def test_build_params_shape() -> None:
    segments = _interview()
    units = split_questions(segments)
    features = compute_features(units, segments)
    params = build_params(features, units)

    assert params["version"] == 1
    assert params["pace"]["wait_ms"] == 2000
    assert params["followup"]["max_depth"] >= 1
    assert params["followup"]["trigger"]
    assert params["mix"]
    assert params["stress"] in {"low", "medium", "high"}
    assert params["style_tags"]
    assert params["sample_questions"]
    assert params["source_features"]["question_count"] == 2


def test_policy_maps_wait_to_silence_and_depth() -> None:
    params = {
        "pace": {"wait_ms": 2500, "interviewer_wpm": 300},
        "followup": {"max_depth": 4, "trigger": ["回答缺少量化数据"]},
        "mix": {"tech": 0.6, "behavior": 0.4},
        "stress": "high",
        "style_tags": ["压迫感强"],
        "patterns": {"opening": "要求自我介绍", "closing": "有反问/薪资环节"},
    }
    policy = build_policy(params, default_silence_ms=900, profile_id=7, profile_name="某公司一面")

    assert policy.silence_ms == 2500  # 面试官等得久 → 我们的 VAD 也给更多沉默时间
    assert policy.max_followup_depth == 4
    assert policy.topic_mix["tech"] == 0.6
    assert policy.profile_id == 7
    assert policy.stress_level == "high"


def test_policy_clamps_extremes() -> None:
    assert build_policy({"pace": {"wait_ms": 50}}).silence_ms == 300
    assert build_policy({"pace": {"wait_ms": 99_999}}).silence_ms == 6000
    assert build_policy({"followup": {"max_depth": 99}}).max_followup_depth == 5
    # 画像里没有等待时长时，用调用方给的默认沉默阈值（这里显式传 900）
    assert build_policy(None, default_silence_ms=900).silence_ms == 900
    assert build_policy({}, default_silence_ms=1100).silence_ms == 1100


def test_policy_block_carries_the_instructions() -> None:
    params = {
        "pace": {"wait_ms": 1500, "interviewer_wpm": 280},
        "followup": {"max_depth": 3, "trigger": ["回答缺少量化数据"]},
        "mix": {"tech": 0.7, "behavior": 0.3},
        "stress": "medium",
        "style_tags": ["紧盯细节连续追问"],
        "patterns": {"opening": "要求自我介绍", "closing": "面试官直接收尾"},
    }
    block = policy_prompt_block(build_policy(params, profile_name="某公司二面"))

    assert "某公司二面" in block
    assert "技术细节" in block  # 题型用中文标签写进 prompt
    assert "70%" in block
    assert "最多追问 3 层" in block
    assert "回答缺少量化数据" in block
    assert "紧盯细节连续追问" in block


def test_policy_summary_is_human_readable() -> None:
    params = {"mix": {"tech": 1.0}, "pace": {"wait_ms": 1200}, "followup": {"max_depth": 2}}
    summary = policy_summary(build_policy(params, profile_name="画像 A"))

    assert summary["profile_name"] == "画像 A"
    assert list(summary["topic_mix"]) == ["技术细节"]  # key 已翻译成中文
    assert summary["silence_ms"] == 1200


@pytest.mark.parametrize("bad_payload", [None, {}, {"pace": "oops"}, {"followup": {"max_depth": None}}])
def test_policy_tolerates_garbage(bad_payload: object) -> None:
    policy = build_policy(bad_payload if isinstance(bad_payload, dict) else None)
    assert 300 <= policy.silence_ms <= 6000
    assert 1 <= policy.max_followup_depth <= 5
