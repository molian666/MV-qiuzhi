"""过滤链与薪资解析。

投递决策错了代价很大（投了外包/重复/已拉黑的公司），
所以每条规则都单独钉一个用例。
"""

from __future__ import annotations

import pytest

from app.apply.filters import FilterRule, JobFacts, evaluate, parse_salary


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15-25K", (15000.0, 25000.0)),
        ("15k-25k", (15000.0, 25000.0)),
        ("15-25K·14薪", (15000.0, 25000.0)),
        ("15000-25000", (15000.0, 25000.0)),
        ("15K", (15000.0, 15000.0)),
        ("8千-1.2万", (8000.0, 12000.0)),
    ],
)
def test_parse_salary_monthly(text: str, expected: tuple[float, float]) -> None:
    assert parse_salary(text) == expected


def test_parse_salary_annual_is_divided_by_twelve() -> None:
    low, high = parse_salary("24-48万/年")  # type: ignore[misc]
    assert round(low) == 20000
    assert round(high) == 40000


@pytest.mark.parametrize("text", ["面议", "薪资面议", "", "待遇从优"])
def test_parse_salary_returns_none_when_unknown(text: str) -> None:
    # 解析不出就返回 None —— 宁可不判断，也不要猜错薪资去拦一个好岗位
    assert parse_salary(text) is None


def _facts(**overrides: object) -> JobFacts:
    base: dict[str, object] = {
        "platform": "boss",
        "company": "某某科技",
        "title": "后端工程师",
        "jd_text": "负责订单系统，要求熟悉 Python 与 Redis",
        "salary_range": "25-40K",
        "city": "杭州",
        "experience_req": "3-5年",
        "degree_req": "本科",
        "hr_active": True,
        "is_headhunter": False,
    }
    base.update(overrides)
    return JobFacts(**base)  # type: ignore[arg-type]


def test_no_rules_passes() -> None:
    assert evaluate(_facts(), []).passed is True


def test_blacklist_blocks() -> None:
    verdict = evaluate(_facts(blacklisted=True), [FilterRule(kind="blacklist", name="黑名单")])
    assert verdict.passed is False
    assert "黑名单" in verdict.reasons[0]


def test_dedup_blocks_reapplying() -> None:
    verdict = evaluate(_facts(already_applied=True), [FilterRule(kind="dedup", name="去重")])
    assert verdict.passed is False


def test_headhunter_blocks_by_default() -> None:
    rules = [FilterRule(kind="headhunter", params={"deny": True}, name="猎头")]
    assert evaluate(_facts(is_headhunter=True), rules).passed is False
    assert evaluate(_facts(is_headhunter=False), rules).passed is True


def test_inactive_hr_blocks() -> None:
    rules = [FilterRule(kind="hr_active", params={"require_active": True}, name="HR活跃")]
    assert evaluate(_facts(hr_active=False), rules).passed is False
    # 抓不到 HR 状态（None）时不拦：信息缺失不等于不活跃
    assert evaluate(_facts(hr_active=None), rules).passed is True


def test_salary_without_overlap_blocks() -> None:
    rules = [FilterRule(kind="salary", params={"min_salary": 30000, "max_salary": 60000}, name="薪资")]
    assert evaluate(_facts(salary_range="15-20K"), rules).passed is False
    assert evaluate(_facts(salary_range="28-40K"), rules).passed is True


def test_salary_unknown_does_not_block() -> None:
    rules = [FilterRule(kind="salary", params={"min_salary": 30000}, name="薪资")]
    # 岗位没写薪资时不拦 —— 很多好岗位不写
    assert evaluate(_facts(salary_range="面议"), rules).passed is True


def test_degree_not_in_allow_list_blocks() -> None:
    rules = [FilterRule(kind="degree", params={"allow": ["本科", "硕士"]}, name="学历")]
    assert evaluate(_facts(degree_req="大专"), rules).passed is False
    assert evaluate(_facts(degree_req="本科"), rules).passed is True


def test_keyword_deny_blocks() -> None:
    rules = [FilterRule(kind="keyword", params={"deny": ["外包", "驻场"]}, name="屏蔽词")]
    assert evaluate(_facts(jd_text="外派驻场开发"), rules).passed is False


def test_keyword_require_absent_blocks() -> None:
    rules = [FilterRule(kind="keyword", params={"require": ["Python"]}, name="期望词")]
    assert evaluate(_facts(jd_text="Java 后端"), rules).passed is False
    assert evaluate(_facts(jd_text="Python 后端"), rules).passed is True


def test_disabled_rule_is_ignored() -> None:
    rules = [FilterRule(kind="keyword", params={"deny": ["外包"]}, enabled=False)]
    assert evaluate(_facts(jd_text="外包"), rules).passed is True


def test_custom_allow_lists_use_exact_match() -> None:
    rules = [FilterRule(kind="city", params={"allow": ["杭州"]})]
    # 未知 kind 不报错、也不拦（向后兼容：新增规则类型时老版本不会炸）
    assert evaluate(_facts(city="北京"), rules).passed is True


def test_multiple_blocks_are_all_reported() -> None:
    rules = [
        FilterRule(kind="blacklist", priority=10, name="黑名单"),
        FilterRule(kind="keyword", params={"deny": ["外包"]}, priority=60, name="屏蔽词"),
    ]
    verdict = evaluate(_facts(blacklisted=True, jd_text="外包驻场"), rules)
    assert verdict.passed is False
    assert verdict.blocked_by == ["黑名单", "屏蔽词"]
    assert len(verdict.reasons) == 2


def test_rules_run_in_priority_order() -> None:
    rules = [
        FilterRule(kind="keyword", params={"deny": ["外包"]}, priority=60, name="屏蔽词"),
        FilterRule(kind="blacklist", priority=10, name="黑名单"),
    ]
    verdict = evaluate(_facts(blacklisted=True, jd_text="外包"), rules)
    assert verdict.blocked_by[0] == "黑名单"
