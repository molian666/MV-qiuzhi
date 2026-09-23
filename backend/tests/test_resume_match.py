"""简历 × JD 匹配诊断、技能词典、年限解析、ATS 体检。

匹配诊断是"投之前该不该改简历"的依据，所以判定标准必须钉死：
权重怎么给、年限怎么算、建议里不许出现编造的数字。
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.analysis.ats import check_ats
from app.analysis.resume_match import (
    WEIGHT_BONUS,
    WEIGHT_DAILY,
    WEIGHT_REQUIREMENT,
    extract_jd_keywords,
    extract_resume_hits,
    match,
    merge_spans,
    required_years,
    resume_years,
    split_sentences,
)
from app.analysis.skill_dict import find_skills, group_label, normalize, skill_group

RESUME = (
    "联系方式：13800138000 / dev@example.com\n"
    "5 年后端开发经验。\n"
    "2021.03-2024.06 某某科技 · 后端开发\n"
    "- 负责订单系统重构，使用 Python 与 Redis，把核心接口 QPS 从 800 提升到 4000。\n"
    "- 主导支付链路高可用改造，故障恢复时间从 30 分钟降到 3 分钟。\n"
    "- 熟悉分布式系统设计，做过限流、降级与链路追踪等稳定性建设。\n"
    "- 带过 3 人小组完成季度目标，负责需求拆分与进度跟踪，配合测试完成上线验证。\n"
    "熟悉 MySQL、消息队列、Docker。"
)

JD = (
    "岗位要求：必须精通 Python；至少 3 年经验；熟悉 Redis 与 MySQL。\n"
    "加分项：了解 Kafka 优先；有 Kubernetes 经验者优先。\n"
    "职责：负责高并发系统的设计与性能优化。"
)


# --------------------------------------------------------------------------- #
# 技能词典
# --------------------------------------------------------------------------- #
def test_find_skills_matches_latin_and_chinese() -> None:
    hits = find_skills("熟悉 Python、Redis 与高并发")
    assert "Python" in hits
    assert "Redis" in hits
    assert "高并发" in hits


def test_alias_is_normalized() -> None:
    assert "Kubernetes" in find_skills("用过 k8s 部署")
    assert "Go" in find_skills("golang 开发")
    assert "PostgreSQL" in find_skills("postgres 性能调优")
    assert normalize("k8s") == "Kubernetes"


def test_word_boundary_avoids_false_positive() -> None:
    # Java 不该被 JavaScript 命中的假阳性拖进来——这会直接改变匹配分
    assert "Java" not in find_skills("精通 JavaScript 与 TypeScript")
    assert "JavaScript" in find_skills("精通 JavaScript")


def test_symbol_languages_are_matched() -> None:
    assert "C++" in find_skills("C++ 开发经验")
    assert "C#" in find_skills("熟悉 C# 与 .NET")


def test_skill_group_lookup() -> None:
    assert skill_group("Redis") == "database"
    assert skill_group("高并发") == "practice"
    assert group_label("database") == "数据库"


# --------------------------------------------------------------------------- #
# JD 抽取与权重
# --------------------------------------------------------------------------- #
def test_extract_jd_keywords_weights_by_tone() -> None:
    keywords = {item.word: item for item in extract_jd_keywords(JD)}

    assert keywords["Python"].weight == WEIGHT_REQUIREMENT  # "必须精通"
    assert keywords["Kafka"].weight == WEIGHT_BONUS  # "优先"
    assert keywords["Redis"].weight == WEIGHT_DAILY  # "熟悉…"不算硬门槛


def test_duplicate_keyword_takes_the_strongest_tone() -> None:
    keywords = {item.word: item for item in extract_jd_keywords("熟悉 Redis。必须精通 Redis。")}
    assert keywords["Redis"].weight == WEIGHT_REQUIREMENT
    assert keywords["Redis"].mentions == 2


def test_keyword_keeps_source_sentence() -> None:
    keywords = extract_jd_keywords(JD)
    python = next(item for item in keywords if item.word == "Python")
    assert "必须精通 Python" in python.sentence


def test_split_sentences() -> None:
    assert split_sentences("第一句。第二句；第三句\n第四句") == ["第一句", "第二句", "第三句", "第四句"]


def test_jd_without_skills() -> None:
    assert extract_jd_keywords("我们希望你踏实肯干，有责任心。") == []


# --------------------------------------------------------------------------- #
# 匹配
# --------------------------------------------------------------------------- #
def test_match_reports_covered_and_missing() -> None:
    result = match(RESUME, JD)

    covered = {item.word for item in result.covered}
    missing = {item.word for item in result.missing}

    assert {"Python", "Redis", "MySQL"} <= covered
    assert {"Kafka", "Kubernetes"} <= missing
    assert result.jd_keyword_count == len(result.covered) + len(result.missing)


def test_missing_is_sorted_by_weight() -> None:
    result = match(RESUME, JD)
    weights = [item.weight for item in result.missing]
    assert weights == sorted(weights, reverse=True)


def test_hard_requirements_come_first_in_suggestions() -> None:
    result = match(RESUME, "岗位要求：必须精通 Kafka。加分项：了解 Rust 优先。")
    assert result.suggestions[0].kind == "add_keyword"
    # 硬性要求排在加分项前面
    assert result.suggestions[0].target == "Kafka"


def test_extra_skills_are_listed_but_not_penalised() -> None:
    result = match("熟悉 Docker 与 Kubernetes", "要求精通 Python")
    # 简历里有、JD 没提的技能只是列出来（供精简参考），不扣分
    assert "Docker" in result.extra
    assert result.score == 0.0  # 没覆盖任何 JD 关键词


def test_score_and_grade_follow_coverage() -> None:
    full = match("熟悉 Python、Redis、MySQL", "要求：Python、Redis、MySQL")
    assert full.score == 100.0
    assert full.grade == "A"

    none = match("熟悉 Vue 与 Photoshop", "要求：必须精通 Python 与 Kafka")
    assert none.score == 0.0
    assert none.grade == "D"


def test_empty_resume_is_handled() -> None:
    result = match("", JD)
    assert result.grade == "D"
    assert "简历是空的" in result.summary


def test_jd_without_keywords_says_so() -> None:
    result = match(RESUME, "希望你踏实肯干，有责任心，能接受挑战。")
    assert result.grade == "—"
    assert "没识别出可比较的技术关键词" in result.summary


def test_missing_groups_summarised() -> None:
    result = match(RESUME, "要求：必须精通 Kafka、Flink、Spark")
    assert "中间件" in result.missing_groups or "数据与 AI" in result.missing_groups


# --------------------------------------------------------------------------- #
# 年限
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3-5年经验", 3),
        ("5年以上经验", 5),
        ("工作经验 4 年", 4),
        ("三年以上", 3),
        ("不限经验", None),
        ("", None),
    ],
)
def test_required_years(text: str, expected: int | None) -> None:
    assert required_years(text) == expected


def test_resume_years_merges_overlapping_spans() -> None:
    overlapping = "2021.03-2023.06 在 A 公司\n2022.01-2022.12 兼职项目"
    # 合并后是 2021.03→2023.06 共 28 个月 ≈ 2.3 年；
    # 不合并会算成 27+12=39 个月（3.3 年），把并行经历重复计了一遍
    assert resume_years(overlapping) == pytest.approx(2.3, abs=0.05)


def test_resume_years_open_span_uses_today() -> None:
    assert resume_years("2024.06-至今 在 B 公司", today=date(2026, 6, 1)) == pytest.approx(2.1, abs=0.05)


def test_resume_years_falls_back_to_stated_years() -> None:
    assert resume_years("有 6 年开发经验") == 6.0


def test_resume_years_unknown() -> None:
    assert resume_years("在某公司做过后端") is None


def test_merge_spans() -> None:
    assert merge_spans([(10, 20), (21, 30), (50, 60)]) == [(10, 30), (50, 60)]
    assert merge_spans([(10, 40), (20, 25)]) == [(10, 40)]


def test_years_shortfall_lowers_score() -> None:
    resume = "2025.01-2026.01 某某公司\n熟悉 Python 与 Redis"
    result = match(resume, "要求：必须精通 Python 与 Redis；5 年以上经验", today=date(2026, 2, 1))

    assert result.years_required == 5
    assert result.years_actual == pytest.approx(1.1, abs=0.1)
    assert result.years_ok is False
    # 覆盖率本来是 100，年限不够要打折
    assert result.score < 100
    assert any(item.kind == "fix_years" for item in result.suggestions)


# --------------------------------------------------------------------------- #
# ATS 体检
# --------------------------------------------------------------------------- #
def test_ats_too_short_is_the_only_issue() -> None:
    issues = check_ats("三年后端经验")
    assert len(issues) == 1
    assert issues[0].kind == "too_short"
    assert issues[0].severity == "error"


def test_ats_clean_resume_has_no_errors() -> None:
    issues = check_ats(RESUME, skill_count=6)
    assert [item for item in issues if item.severity == "error"] == []


def test_ats_missing_contact_details() -> None:
    text = RESUME.replace("联系方式：13800138000 / dev@example.com\n", "")
    issues = check_ats(text, skill_count=6)
    assert any(item.kind == "no_contact" and item.severity == "error" for item in issues)


def test_ats_phone_only_is_a_warning() -> None:
    text = RESUME.replace(" / dev@example.com", "")
    issues = {item.kind: item for item in check_ats(text, skill_count=6)}
    assert issues["no_email"].severity == "warning"


def test_ats_fullwidth_characters_are_an_error() -> None:
    # 全角不是风格问题：ATS 的关键词匹配对全角不命中，这个词等于白写
    text = RESUME + "\n熟悉 Ｐｙｔｈｏｎ 与 ２０２１ 年技术栈"
    issues = {item.kind: item for item in check_ats(text, skill_count=6)}
    assert issues["fullwidth"].severity == "error"


def test_ats_missing_timeline_is_an_error() -> None:
    text = (
        "联系方式：13800138000 / dev@example.com\n"
        "我做过很多后端项目，熟悉 Python、Redis、MySQL、Kafka、Docker，负责过订单系统，"
        "也做过支付链路的改造，带过三个人的小组，平时喜欢研究性能优化，"
        "把接口的响应时间降低了不少，稳定性也提升了很多，团队里的人都觉得还不错。"
        "平时也负责新人带教与代码审查，参与需求评审，配合测试完成上线验证工作。"
    )
    issues = {item.kind: item for item in check_ats(text, skill_count=5)}
    assert issues["no_timeline"].severity == "error"


def test_ats_detects_bloated_block() -> None:
    body = "负责系统维护与优化，" * 100
    text = f"联系方式：13800138000 / dev@example.com\n2021.03-2024.06 某某公司\n{body}"
    issues = {item.kind: item for item in check_ats(text, skill_count=6)}
    assert "bloated_block" in issues


def test_ats_detects_missing_metric() -> None:
    text = (
        "联系方式：13800138000 / dev@example.com\n"
        "2021.03-2024.06 某某公司 · 后端开发\n"
        "负责订单系统的日常维护与功能开发，熟悉 Python、Redis、MySQL、Kafka、Docker。\n"
        "参与团队的需求评审与代码审查，配合测试完成上线验证，负责线上问题的日常排查与修复。"
    )
    issues = {item.kind: item for item in check_ats(text, skill_count=6)}
    assert "no_metric" in issues


def test_ats_detects_mixed_bullets() -> None:
    text = RESUME + "\n• 一条\n· 另一条\n* 第三条"
    issues = {item.kind: item for item in check_ats(text, skill_count=6)}
    assert "mixed_bullets" in issues


def test_ats_few_keywords_warning() -> None:
    text = RESUME + "\n" + "日常协作与沟通顺畅。" * 30
    issues = {item.kind: item for item in check_ats(text, skill_count=2)}
    assert "few_keywords" in issues


def test_ats_errors_are_sorted_first() -> None:
    issues = check_ats(RESUME + "\n熟悉 Ｐｙｔｈｏｎ", skill_count=6)
    severities = [item.severity for item in issues]
    assert severities == sorted(severities, key=lambda value: {"error": 0, "warning": 1, "info": 2}[value])


# --------------------------------------------------------------------------- #
# 建议：不许编造
# --------------------------------------------------------------------------- #
def test_keyword_suggestion_draft_does_not_fabricate_numbers() -> None:
    result = match(RESUME, "要求：必须精通 Kafka，把吞吐提升 300%。")
    drafts = [item for item in result.suggestions if item.kind == "add_keyword"]
    assert drafts

    for item in drafts:
        # 草稿里只允许出现占位符，不许有具体的百分比/倍数——那等于替用户编成果
        assert not re.search(r"\d+\s*(?:%|％|倍)", item.draft)
        assert "不会替你编" in item.draft
        assert "「" in item.draft  # 给了句式模板


def test_suggestion_points_to_a_real_experience() -> None:
    result = match(RESUME, "要求：必须精通 Kafka")
    item = next(entry for entry in result.suggestions if entry.kind == "add_keyword")
    # where 指向的是简历里真实存在的经历，而不是空泛的"工作经历中"
    assert "这段经历里" in item.where
    assert "订单" in item.where or "支付" in item.where or "某某" in item.where


def test_ats_errors_become_suggestions() -> None:
    short = match("三年后端经验", JD)
    kinds = {item.kind for item in short.suggestions}
    assert "fix_ats" in kinds


def test_extract_resume_hits_records_evidence() -> None:
    hits = extract_resume_hits(RESUME)
    assert "Redis" in hits
    assert "Redis" in hits["Redis"].evidence
    assert hits["Python"].has_metric is True
