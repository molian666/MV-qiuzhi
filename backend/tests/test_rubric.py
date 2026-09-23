"""Rubric 定义的测试。

维度是产品决策：键名被前端、报告 JSON、趋势图共用，改动必须是有意识的。
"""

from __future__ import annotations

from app.analysis.rubric import (
    DIMENSIONS,
    build_rubric_prompt,
    dimension_keys,
    is_valid_dimension,
    label_for,
)


def test_dimensions_are_complete_and_unique() -> None:
    keys = dimension_keys()
    assert keys == (
        "relevance",
        "structure",
        "quantification",
        "depth",
        "delivery",
        "time_control",
        "initiative",
    )
    assert len(set(keys)) == len(keys)


def test_every_dimension_has_focus_and_three_anchors() -> None:
    for dimension in DIMENSIONS:
        assert dimension.label
        assert dimension.focus
        assert len(dimension.anchors) == 3
        assert all(anchor.strip() for anchor in dimension.anchors)


def test_rubric_prompt_contains_dimensions_and_anchors() -> None:
    prompt = build_rubric_prompt()
    for dimension in DIMENSIONS:
        assert dimension.label in prompt
        assert dimension.key in prompt
        for anchor in dimension.anchors:
            assert anchor in prompt
    assert "1 分" in prompt
    assert "3 分" in prompt
    assert "5 分" in prompt


def test_label_and_validity_helpers() -> None:
    assert label_for("depth") == "技术深度与正确性"
    assert label_for("quantification") == "信息密度与量化"
    # 未知 key 原样返回：报告页宁可显示英文 key，也不要显示空白
    assert label_for("nope") == "nope"
    assert is_valid_dimension("delivery")
    assert not is_valid_dimension("self_invented_dimension")
