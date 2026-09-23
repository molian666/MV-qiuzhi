"""谈判简报：金额归一化、底线守住率、让步轨迹、战术识别。

简报的价值全在"每个结论都能指向具体哪一句、哪个数"，
所以这里重点测**金额归一化**（它错了后面全错）与守住率判定。
"""

from __future__ import annotations

import pytest

from app.analysis.negotiation import (
    TACTICS,
    BriefTurn,
    build_brief,
    extract_amounts,
    tactic_options,
)


def _dialogue(*pairs: tuple[str, str]) -> list[BriefTurn]:
    return [BriefTurn(speaker=speaker, text=text) for speaker, text in pairs]


# --------------------------------------------------------------------------- #
# 金额归一化（整个简报的地基）
# --------------------------------------------------------------------------- #
def test_k_suffix_is_monthly() -> None:
    amounts = extract_amounts("另一个 offer 给 35k")
    assert amounts[0].annual == pytest.approx(35_000 * 12)
    assert amounts[0].kind == "monthly"


def test_wan_with_no_cue_uses_magnitude() -> None:
    assert extract_amounts("我期望 40 万")[0].annual == pytest.approx(400_000)
    assert extract_amounts("月薪 3.5 万")[0].annual == pytest.approx(35_000 * 12)


def test_nearest_cue_wins_in_a_mixed_sentence() -> None:
    """「月薪 3.5 万，年薪 42 万」——固定窗口会把两个线索都圈进来，必须按距离判断。

    这是实测踩到的 bug：42 万被误判成月薪，整份简报的最终报价就都错了。
    """
    amounts = {item.raw: item for item in extract_amounts("月薪 3.5 万，年薪 42 万")}
    assert amounts["3.5 万"].kind == "monthly"
    assert amounts["42 万"].kind == "annual"
    assert amounts["42 万"].annual == pytest.approx(420_000)


def test_annual_cues() -> None:
    assert extract_amounts("年包 50 万左右")[0].annual == pytest.approx(500_000)
    assert extract_amounts("一年 16 薪，月薪 25000")[0].annual == pytest.approx(300_000)


def test_bare_number_monthly() -> None:
    assert extract_amounts("月薪 25000")[0].annual == pytest.approx(300_000)


def test_cue_too_far_away_is_not_a_cue() -> None:
    """「月薪加年终，整体大概在 36.0 万」里的「月薪」离数字 10 个字，不能拿它当线索。

    实测这个 bug 让"对方最终报价"变成 432 万（36 万 × 12）——整份简报的结论都跟着错。
    """
    text = "我们这个岗位的薪酬结构是月薪加年终，整体大概在 36.0 万 到 45.0 万 之间"
    amounts = {item.raw: item for item in extract_amounts(text)}

    assert amounts["36.0 万"].kind == "annual"
    assert amounts["36.0 万"].annual == pytest.approx(360_000)
    assert amounts["45.0 万"].annual == pytest.approx(450_000)


def test_nearby_cue_still_wins() -> None:
    # 常见写法里线索词紧贴数字，仍然要生效
    assert extract_amounts("月薪 3.5 万")[0].kind == "monthly"
    assert extract_amounts("月薪是 3.5 万")[0].kind == "monthly"
    assert extract_amounts("我期望的月薪不低于 3.5 万")[0].kind == "monthly"


def test_no_amounts() -> None:
    assert extract_amounts("我们再研究一下，明天回复你") == []
    assert extract_amounts("") == []


# --------------------------------------------------------------------------- #
# 简报
# --------------------------------------------------------------------------- #
def test_bottom_line_held() -> None:
    brief = build_brief(
        _dialogue(
            ("interviewer", "这个岗位年薪大概 35 万"),
            ("candidate", "我的期望是 45 万，市场行情也在这个区间"),
            ("interviewer", "最多能给到 42 万，再高要审批"),
            ("candidate", "42 万可以接受"),
        ),
        bottom_line=400_000,
        target=500_000,
    )
    assert brief.final_offer == pytest.approx(420_000)
    assert brief.bottom_line_held is True
    assert brief.gap_to_target == pytest.approx(-80_000)


def test_bottom_line_broken() -> None:
    brief = build_brief(
        _dialogue(
            ("interviewer", "我们能给 32 万"),
            ("candidate", "好吧，32 万也可以"),
        ),
        bottom_line=400_000,
        target=500_000,
    )
    assert brief.bottom_line_held is False
    assert any("底线" in issue for issue in brief.issues)


def test_bottom_line_unknown_without_numbers() -> None:
    brief = build_brief(
        _dialogue(("interviewer", "我们再研究一下"), ("candidate", "好的，我等你消息")),
        bottom_line=400_000,
        target=500_000,
    )
    assert brief.bottom_line_held is None
    assert brief.final_offer is None
    assert any("没报过任何数字" in issue for issue in brief.issues)


def test_first_ask_near_bottom_line_is_flagged() -> None:
    brief = build_brief(
        _dialogue(
            ("candidate", "我期望 41 万"),
            ("interviewer", "我们给到 40 万"),
        ),
        bottom_line=400_000,
        target=500_000,
    )
    assert brief.first_ask == pytest.approx(410_000)
    assert any("贴着底线" in issue for issue in brief.issues)


def test_fast_concession_is_flagged() -> None:
    brief = build_brief(
        _dialogue(
            ("candidate", "我期望 60 万"),
            ("candidate", "50 万也行"),
            ("candidate", "45 万可以谈"),
        ),
        bottom_line=400_000,
        target=600_000,
    )
    assert brief.first_ask == pytest.approx(600_000)
    assert brief.last_ask == pytest.approx(450_000)
    assert brief.concession_ratio > 0
    assert any("让步幅度" in issue for issue in brief.issues)


def test_tactics_are_recognised() -> None:
    brief = build_brief(
        _dialogue(
            ("candidate", "想先了解一下这个岗位的薪资区间是多少"),
            ("candidate", "按照市场行情，这个级别普遍 40 万以上"),
            ("candidate", "如果月薪有上限，能不能谈谈签字费和股票"),
        ),
        bottom_line=300_000,
        target=500_000,
    )
    used = set(brief.tactics_used)
    assert {"ask_range", "market_data", "total_package"} <= used
    assert "walk_away" in brief.tactics_missed


def test_missing_total_package_talk_is_flagged() -> None:
    brief = build_brief(
        _dialogue(("candidate", "我期望 40 万"), ("interviewer", "我们只能给 35 万，已经到上限了")),
        bottom_line=300_000,
        target=450_000,
    )
    assert any("签字费" in issue for issue in brief.issues)


def test_score_is_bounded_and_rewards_holding_the_line() -> None:
    held = build_brief(
        _dialogue(
            ("candidate", "想先了解一下薪资区间"),
            ("interviewer", "40 万左右"),
            ("candidate", "低于 42 万我无法接受"),
            ("interviewer", "那给你 43 万"),
        ),
        bottom_line=420_000,
        target=500_000,
    )
    broken = build_brief(
        _dialogue(("candidate", "我期望 30 万"), ("interviewer", "好，那就 28 万")),
        bottom_line=420_000,
        target=500_000,
    )
    assert 0 <= broken.score <= 100
    assert 0 <= held.score <= 100
    assert held.score > broken.score


def test_brief_has_a_caveat() -> None:
    brief = build_brief(_dialogue(("candidate", "40 万")), bottom_line=300_000, target=500_000)
    # 不能对"对方报价是否合理"下结论
    assert "不评价对方" in brief.caveat


def test_summary_mentions_key_numbers() -> None:
    brief = build_brief(
        _dialogue(("candidate", "我期望 45 万"), ("interviewer", "最终给你 42 万")),
        bottom_line=400_000,
        target=500_000,
    )
    assert "42.0 万" in brief.summary or "42 万" in brief.summary


def test_accepts_plain_dicts() -> None:
    brief = build_brief(
        [{"speaker": "candidate", "text": "我期望 40 万"}, {"speaker": "interviewer", "text": "给 38 万"}],
        bottom_line=350_000,
        target=450_000,
    )
    assert brief.first_ask == pytest.approx(400_000)


def test_tactic_options_shape() -> None:
    options = tactic_options()
    assert len(options) == len(TACTICS)
    assert all(item["label"] and item["summary"] and item["when"] for item in options)
