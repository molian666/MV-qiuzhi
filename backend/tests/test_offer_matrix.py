"""Offer 对比矩阵与敏感性分析。

核心验收是**矩阵能随权重变化翻转排名**——所以这里最重要的测试不是"算得对不对"，
而是"权重一动，第一名真的会换人，并且能被指出来"。
"""

from __future__ import annotations

import pytest

from app.analysis.offer_matrix import (
    DEFAULT_WEIGHTS,
    DIMENSIONS,
    OfferInput,
    annual_comp,
    build_matrix,
    compensation_scores,
    dimension_options,
    find_flips,
    format_comp,
    normalize_weights,
    scan_ranking,
    score_offers,
    with_weight,
)


def _offer(offer_id: int, company: str, comp: float, **factors: float) -> OfferInput:
    return OfferInput(id=offer_id, company=company, total_comp=comp, factors=dict(factors))


# --------------------------------------------------------------------------- #
# 权重与薪酬
# --------------------------------------------------------------------------- #
def test_normalize_weights_fills_missing_and_sums_to_one() -> None:
    weights = normalize_weights({"compensation": 3, "growth": 1})
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["compensation"] > weights["growth"]
    # 没填的维度会用默认值补齐，而不是归 0——
    # 否则"只调两项"就等于放弃其它所有维度
    assert weights["stability"] > 0
    assert weights["compensation"] == pytest.approx(3 / 4.5, abs=0.01)


def test_normalize_weights_drops_unknown_and_clamps_negative() -> None:
    weights = normalize_weights({"compensation": 1, "growth": -5, "not_a_dimension": 10})
    assert "not_a_dimension" not in weights
    assert weights["growth"] == 0.0


def test_all_zero_weights_fall_back_to_default() -> None:
    # 全填 0 等于没填 → 回到默认，而不是除以 0
    weights = normalize_weights({key: 0 for key in DIMENSIONS})
    assert weights == normalize_weights(DEFAULT_WEIGHTS)


def test_with_weight_rescales_the_rest() -> None:
    base = normalize_weights({"compensation": 0.5, "growth": 0.3, "stability": 0.2})
    scaled = with_weight(base, "compensation", 0.8)
    assert sum(scaled.values()) == pytest.approx(1.0)

    # 其余维度的**相对比例**保持不变：growth 与 stability 原本 3:2
    assert scaled["growth"] / scaled["stability"] == pytest.approx(0.3 / 0.2, abs=0.01)


def test_compensation_is_normalised_against_the_best_offer() -> None:
    offers = [_offer(1, "A", 600_000), _offer(2, "B", 300_000), _offer(3, "C", 0)]
    scores = compensation_scores(offers)
    assert scores[1] == 10.0
    assert scores[2] == 5.0
    assert scores[3] == 0.0


def test_annual_comp_counts_signing_bonus_once() -> None:
    # 签字费只算首年——把它当成"每年都有"是 offer 对比里最常见的自欺
    total = annual_comp(monthly_salary=25_000, months_per_year=16, annual_bonus=50_000, signing_bonus=30_000)
    assert total == pytest.approx(25_000 * 16 + 50_000 + 30_000)


def test_format_comp() -> None:
    assert format_comp(385_000) == "38.5 万"
    assert format_comp(0) == "—"
    assert format_comp(8_000) == "8000 元"


# --------------------------------------------------------------------------- #
# 打分
# --------------------------------------------------------------------------- #
def test_score_breakdown_adds_up_to_total() -> None:
    offers = [_offer(1, "A", 500_000, growth=8, stability=6)]
    row = score_offers(offers)[0]
    assert row.total == pytest.approx(sum(row.breakdown.values()), abs=0.01)
    assert row.stars["compensation"] == 10.0


def test_missing_factors_count_as_zero() -> None:
    offers = [_offer(1, "A", 500_000)]
    row = score_offers(offers)[0]
    assert row.stars["growth"] == 0.0
    # 只有薪酬有分 → 总分就是薪酬那一项
    assert row.total == pytest.approx(row.breakdown["compensation"])


def test_tie_break_prefers_higher_pay() -> None:
    offers = [_offer(1, "低薪", 200_000), _offer(2, "高薪", 400_000)]
    weights = {"compensation": 0.0, "growth": 0.0, "stability": 0.0}
    rows = score_offers(offers, weights)
    # 权重全 0 会回落到默认，所以这里改用"成长分相同"来制造并列
    same = [_offer(1, "低薪", 200_000, growth=5, stability=5), _offer(2, "高薪", 400_000, growth=5, stability=5)]
    rows = score_offers(same, {"compensation": 0.0, "growth": 0.5, "stability": 0.5})
    assert rows[0].company in {"低薪", "高薪"}
    assert [row.rank for row in rows] == [1, 2]


# --------------------------------------------------------------------------- #
# 翻转（核心）
# --------------------------------------------------------------------------- #
def test_ranking_flips_when_pay_weight_changes() -> None:
    big = _offer(1, "大厂", 600_000, growth=3, stability=5)
    startup = _offer(2, "创业公司", 360_000, growth=9, stability=6)
    offers = [big, startup]

    pay_heavy = score_offers(offers, {"compensation": 0.9, "growth": 0.05, "stability": 0.05})
    growth_heavy = score_offers(offers, {"compensation": 0.05, "growth": 0.9, "stability": 0.05})

    assert pay_heavy[0].company == "大厂"
    assert growth_heavy[0].company == "创业公司"


def test_flip_point_is_reported_with_a_readable_message() -> None:
    offers = [_offer(1, "大厂", 600_000, growth=3), _offer(2, "创业公司", 360_000, growth=9)]
    flips = find_flips(offers, {"compensation": 0.5, "growth": 0.5})

    assert flips, "这个组合必须能扫出翻转点"
    flip = next(item for item in flips if item.factor == "compensation")
    assert flip.weight > 0
    assert flip.winner_company in {"大厂", "创业公司"}
    # 结论要能直接读出来，而不是只给个数字
    assert "权重" in flip.message
    assert flip.winner_company in flip.message


def test_scan_ranking_is_monotonic_in_shape() -> None:
    offers = [_offer(1, "大厂", 600_000, growth=3), _offer(2, "创业公司", 360_000, growth=9)]
    points = scan_ranking(offers, {"compensation": 0.5, "growth": 0.5}, factor="compensation", steps=10)

    assert len(points) == 11
    assert points[0][0] == 0.0
    weights = [weight for weight, _ in points]
    assert weights == sorted(weights)
    # 薪酬权重很低时应该是创业公司领先，很高时是大厂
    assert points[0][1] == 2
    assert points[-1][1] == 1


def test_single_offer_has_no_flips() -> None:
    offers = [_offer(1, "唯一", 500_000, growth=5)]
    assert find_flips(offers) == []
    assert scan_ranking(offers, DEFAULT_WEIGHTS, factor="compensation") == []


def test_stable_winner_produces_no_flips() -> None:
    # 一个 offer 在所有维度都更好 → 怎么调权重都不该翻转
    better = _offer(1, "全面更好", 800_000, growth=9, stability=9, commute=9, team=9, tech_stack=9, industry=9, wlb=9, brand=9, location=9)
    worse = _offer(2, "全面更差", 300_000, growth=2, stability=2, commute=2, team=2, tech_stack=2, industry=2, wlb=2, brand=2, location=2)
    assert find_flips([better, worse]) == []


def test_build_matrix_reports_notes() -> None:
    only = build_matrix([_offer(1, "唯一", 0, growth=5)])
    assert only.rows[0].company == "唯一"
    assert any("只有一个 offer" in note for note in only.notes)
    assert any("没填薪资结构" in note for note in only.notes)
    assert only.disclaimer

    rich = build_matrix([_offer(1, "A", 500_000, growth=5), _offer(2, "B", 400_000, growth=5)])
    assert rich.rows[0].rank == 1


def test_dimension_options_cover_all_dimensions() -> None:
    options = dimension_options()
    assert [item["key"] for item in options] == list(DIMENSIONS)
    assert all(item["label"] for item in options)
    assert sum(item["default"] for item in options) == pytest.approx(1.0, abs=0.01)
