"""压力模式、面试官风格、连环面、中英双语。

这些配置直接决定"这场面试怎么问"，配错了训练就失去意义
（比如连环面里两个角色音色一样，那只是换了个名字继续问）。
"""

from __future__ import annotations

import pytest

from app.analysis.interviewer_styles import (
    STYLE_ORDER,
    STYLES,
    TOPIC_KEY_LABELS,
    TOPIC_KEYS,
    normalize_mix,
    resolve_style,
    style_options,
    style_params,
    style_prompt_block,
)
from app.analysis.locale import (
    SUPPORTED_LOCALES,
    dimension_label,
    filler_words,
    locale_options,
    locale_prompt_block,
    resolve_locale,
    topic_label,
)
from app.analysis.panels import MAX_TOTAL_ROUNDS, build_panel, panel_prompt_block, parse_panel
from app.analysis.pressure import (
    MAX_DEPTH_LIMIT,
    PRESSURE_MODES,
    PressureConfig,
    budget_note,
    clamp_depth,
    exceeded_time_limit,
    mode_options,
    normalize_pressure,
    pressure_prompt_block,
    remaining_seconds,
    should_interrupt,
)
from app.analysis.speech_features import TurnSpan, analyze, count_fillers


# --------------------------------------------------------------------------- #
# 压力模式
# --------------------------------------------------------------------------- #
def test_modes_are_independent_switches() -> None:
    config = PressureConfig(deep_drill=True)
    assert config.modes() == ["deep_drill"]
    assert config.enabled is True

    combined = PressureConfig(deep_drill=True, interrupt=True, time_limit=True)
    assert combined.modes() == list(PRESSURE_MODES)


def test_disabled_by_default() -> None:
    config = PressureConfig()
    assert config.enabled is False
    assert config.modes() == []
    assert pressure_prompt_block(config) == ""


def test_normalize_clamps_out_of_range_values() -> None:
    # 前端输入与历史数据都不可信
    config = normalize_pressure({"deep_drill": True, "max_depth": 99, "interrupt_after_ms": 10, "answer_seconds": 9999})
    assert config.max_depth == MAX_DEPTH_LIMIT
    assert config.interrupt_after_ms >= 3_000
    assert config.answer_seconds <= 600


def test_normalize_accepts_dict_and_instance() -> None:
    assert normalize_pressure(PressureConfig(deep_drill=True)).deep_drill is True
    assert normalize_pressure(None).enabled is False
    assert normalize_pressure({"interrupt": True}).interrupt is True


def test_prompt_block_only_mentions_enabled_modes() -> None:
    block = pressure_prompt_block(PressureConfig(deep_drill=True, max_depth=4))
    assert "追问到底" in block
    assert "4 层" in block
    assert "打断" not in block
    assert "限时" not in block


def test_english_prompt_block() -> None:
    block = pressure_prompt_block(PressureConfig(time_limit=True, answer_seconds=60), locale="en")
    assert "Time limit" in block
    assert "60 seconds" in block


def test_should_interrupt_only_when_mode_enabled() -> None:
    off = PressureConfig(interrupt=False, interrupt_after_ms=5_000)
    assert should_interrupt(60_000, off) is False

    on = PressureConfig(interrupt=True, interrupt_after_ms=8_000)
    assert should_interrupt(7_999, on) is False
    assert should_interrupt(8_000, on) is True


def test_time_limit_judgement() -> None:
    config = PressureConfig(time_limit=True, answer_seconds=90)
    assert exceeded_time_limit(89_000, config) is False
    assert exceeded_time_limit(90_000, config) is True
    assert exceeded_time_limit(10_000, PressureConfig()) is False


def test_remaining_seconds_only_when_limited() -> None:
    assert remaining_seconds(30_000, PressureConfig()) is None

    config = PressureConfig(time_limit=True, answer_seconds=90)
    assert remaining_seconds(30_000, config) == 60
    assert remaining_seconds(120_000, config) == 0  # 不会出现负数


def test_clamp_depth_respects_the_cap() -> None:
    config = PressureConfig(deep_drill=True, max_depth=3)
    assert clamp_depth(0, config) == 1
    assert clamp_depth(2, config) == 3
    assert clamp_depth(3, config) == 3  # 到顶就不再往下追

    assert clamp_depth(5, PressureConfig()) == 0  # 没开模式就没有深度


def test_budget_note() -> None:
    assert budget_note(PressureConfig(time_limit=True, answer_seconds=60)) == "这题限时 60 秒"
    assert "追 5 层" in budget_note(PressureConfig(deep_drill=True))
    assert budget_note(PressureConfig()) == ""


def test_mode_options_cover_all_modes() -> None:
    options = mode_options()
    assert [item["key"] for item in options] == list(PRESSURE_MODES)
    assert all(item["label"] and item["summary"] and item["defaults"] for item in options)


# --------------------------------------------------------------------------- #
# 面试官风格
# --------------------------------------------------------------------------- #
def test_every_style_has_a_distinct_voice() -> None:
    voices = [STYLES[key].voice for key in STYLE_ORDER]
    assert len(voices) == len(set(voices)), f"音色重复会让多角色失去意义：{voices}"


def test_resolve_style_falls_back_instead_of_failing() -> None:
    # 风格是增强项：未知 key 不该让会话开不起来
    assert resolve_style("nope").key == "technical"
    assert resolve_style(None).key == "technical"
    assert resolve_style("stress").key == "stress"


def test_normalize_mix_drops_unknown_and_sums_to_one() -> None:
    mix = normalize_mix({"tech": 3, "scenario": 1, "not_a_topic": 5})
    assert set(mix) == {"tech", "scenario"}
    assert sum(mix.values()) == pytest.approx(1.0)
    assert normalize_mix({}) == {}


def test_design_is_no_longer_a_question_type() -> None:
    """系统设计题型已随白板一起移除：配比里给了也不该生效。"""
    assert "design" not in TOPIC_KEYS
    assert "design" not in TOPIC_KEY_LABELS
    assert normalize_mix({"tech": 1, "design": 1}) == {"tech": pytest.approx(1.0)}
    for style in STYLES.values():
        assert "design" not in style.question_mix, style.key


def test_style_prompt_block_carries_actionable_rules() -> None:
    block = style_prompt_block(STYLES["stress"])
    assert "压力面" in block
    assert "追" in block
    # 光有语气没有规则等于没给
    assert "依据是什么" in block
    # 压力面也不能变成人身攻击
    assert "人身攻击" in block


def test_english_style_prompt_block() -> None:
    block = style_prompt_block(STYLES["hr"], locale="en")
    assert "Interviewer" in block
    assert "Focus:" in block


def test_style_params_feed_the_pacing_pipeline() -> None:
    params = style_params(STYLES["technical"])
    assert params["style_key"] == "technical"
    assert params["followup_depth"] == 3
    assert set(params["topic_mix"]) <= {"project_deep", "tech", "design", "algorithm"}


def test_style_options_shape() -> None:
    options = style_options()
    assert len(options) == len(STYLE_ORDER)
    assert all(item["voice_label"] for item in options)


# --------------------------------------------------------------------------- #
# 连环面
# --------------------------------------------------------------------------- #
def test_default_panel_engine() -> None:
    plan = build_panel()
    assert plan.total_rounds() == 9
    assert [stage.style_key for stage in plan.stages] == ["technical", "cross", "manager", "hr"]


def test_stage_switches_by_round() -> None:
    plan = build_panel([("technical", 2), ("hr", 1)])
    assert plan.style_for(0).key == "technical"
    assert plan.style_for(1).key == "technical"
    assert plan.style_for(2).key == "hr"
    assert plan.style_for(99).key == "hr"  # 超出就停在最后一位，不该返回 None 崩掉


def test_handover_is_the_first_round_of_each_new_stage() -> None:
    plan = build_panel([("technical", 2), ("hr", 2)])
    assert plan.is_handover(0) is False
    assert plan.is_handover(1) is False
    assert plan.is_handover(2) is True
    assert plan.is_handover(3) is False


def test_voices_stay_unique_even_with_duplicate_styles() -> None:
    # 用户完全可能配两轮技术面——那时候必须换音色，否则听不出换人
    plan = build_panel([("technical", 2), ("technical", 2), ("hr", 2)])
    voices = [stage.voice for stage in plan.stages]
    assert len(voices) == len(set(voices)), voices


def test_voice_for_each_round_matches_its_stage() -> None:
    plan = build_panel([("technical", 1), ("hr", 1)])
    assert plan.voice_for(0) == plan.stages[0].voice
    assert plan.voice_for(1) == plan.stages[1].voice


def test_panel_caps_total_rounds() -> None:
    plan = build_panel([("technical", 100), ("hr", 100)])
    assert plan.total_rounds() <= MAX_TOTAL_ROUNDS


def test_empty_spec_falls_back_to_default_panel() -> None:
    # 空列表 = 没指定 → 用默认编排（技术 → 交叉 → 主管 → HR），而不是开一场没人问的面试
    plan = build_panel([])
    assert [stage.style_key for stage in plan.stages] == ["technical", "cross", "manager", "hr"]
    assert plan.total_rounds() > 0


def test_parse_panel_accepts_multiple_shapes() -> None:
    assert parse_panel(None) is None
    assert parse_panel([]) is None
    assert parse_panel([("technical", 2)]).total_rounds() == 2
    assert parse_panel([{"style_key": "hr", "rounds": 3}]).total_rounds() == 3
    assert parse_panel({"stages": [{"style_key": "hr", "rounds": 2}]}).total_rounds() == 2


def test_timeline_and_current_for_frontend() -> None:
    plan = build_panel([("technical", 2), ("hr", 1)])
    timeline = plan.timeline()
    assert timeline[0]["start_round"] == 0
    assert timeline[1]["start_round"] == 2
    assert timeline[1]["voice_label"]

    current = plan.current(2)
    assert current["index"] == 1
    assert current["total"] == 2
    assert current["is_handover"] is True


def test_panel_prompt_block_tells_who_is_asking() -> None:
    plan = build_panel([("technical", 2), ("hr", 2)])
    handover = panel_prompt_block(plan, 2)
    assert "第 2 位面试官" in handover
    assert "不要重复上一位问过的内容" in handover
    assert "上一位面试官是「技术面」" in handover

    not_handover = panel_prompt_block(plan, 3)
    assert "这是你第一次提问" not in not_handover


def test_english_panel_prompt_block() -> None:
    plan = build_panel([("technical", 1), ("hr", 1)])
    block = panel_prompt_block(plan, 1, locale="en")
    assert "You are interviewer 2 of 2" in block


# --------------------------------------------------------------------------- #
# 中英双语
# --------------------------------------------------------------------------- #
def test_resolve_locale_falls_back_to_chinese() -> None:
    assert resolve_locale("en").key == "en"
    assert resolve_locale("EN").key == "en"
    assert resolve_locale("fr").key == "zh"
    assert resolve_locale(None).key == "zh"


def test_locale_prompt_block_switches_language() -> None:
    assert "中文" in locale_prompt_block("zh")
    assert "English" in locale_prompt_block("en")


def test_dimension_and_topic_labels_follow_locale() -> None:
    assert dimension_label("depth", "en") == "Technical Depth"
    assert dimension_label("depth", "zh", fallback="技术深度与正确性") == "技术深度与正确性"
    assert topic_label("scenario", "en") == "Scenario"
    assert topic_label("scenario", "zh", fallback="场景题") == "场景题"


def test_locale_options_cover_all_supported() -> None:
    assert [item["key"] for item in locale_options()] == list(SUPPORTED_LOCALES)


def test_english_fillers_use_word_boundaries() -> None:
    """不加词边界的话「um」会命中「number」、「er」会命中「over」——统计直接失真。"""
    text = "The number of users is over 1000. Um, you know, I mean it scales well."
    counts = {item.word: item.count for item in count_fillers(text, locale="en")}

    assert counts["um"] == 1  # 只有句首那个是真的填充词
    assert "er" not in counts  # "over" 里的 er 不算
    assert counts["you know"] == 1
    assert counts["i mean"] == 1


def test_chinese_fillers_use_substring() -> None:
    counts = {item.word: item.count for item in count_fillers("嗯嗯，那个那个我们就那样做了", locale="zh")}
    assert counts["嗯"] == 2
    assert counts["那个"] == 2


def test_filler_words_switch_by_locale() -> None:
    zh_strong, _ = filler_words("zh")
    en_strong, _ = filler_words("en")
    assert "嗯" in zh_strong
    assert "um" in en_strong
    assert "嗯" not in en_strong


def test_expression_analysis_uses_locale_fillers() -> None:
    turns = [
        TurnSpan(
            index=0,
            speaker="candidate",
            text=("Um, so basically the number of requests is over a million and, you know, we scaled it. " * 12),
            start_ms=0,
            end_ms=90_000,
        )
    ]
    report = analyze(turns, locale="en")
    words = {item.word for item in report.fillers}

    assert "um" in words
    assert "you know" in words
    assert "er" not in words  # 来自 "over"，不该被算进来
