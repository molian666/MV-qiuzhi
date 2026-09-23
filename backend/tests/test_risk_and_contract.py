"""公司风险提示与合同提醒。

验收里的硬要求：**避坑条目全部可点开原始来源**。
所以这里把它写成断言——每个信号必须指向至少一个可核实来源，
且所有来源都有真实 URL。靠人工检查这种事，早晚会漏。
"""

from __future__ import annotations

from app.analysis.company_risk import (
    SOURCE_LABELS,
    SOURCES,
    RULES,
    scan_text,
    signal_catalog,
    source_options,
)
from app.analysis.contract_check import (
    CLAUSES,
    build_report,
    clause_options,
)


# --------------------------------------------------------------------------- #
# 风险提示
# --------------------------------------------------------------------------- #
def test_every_signal_points_at_a_real_source() -> None:
    """验收要求：避坑条目全部可点开原始来源。"""
    for rule in signal_catalog():
        assert rule["sources"], f"「{rule['label']}」没有指向任何来源"
        for label in rule["sources"]:
            assert label in SOURCE_LABELS, f"「{label}」不在来源清单里"


def test_every_source_has_a_real_url() -> None:
    for source in SOURCES:
        assert source.url.startswith("http")
        assert source.what
        # 工具没有访问过这些来源 —— 必须诚实标注，而不是假装核实过
        assert source.verified is False


def test_scan_results_carry_sources_and_actions() -> None:
    report = scan_text("急招后端，入职要交培训费 2000，试用期不上社保")
    assert report.signals

    for signal in report.signals:
        assert signal.sources
        assert all(label in SOURCE_LABELS for label in signal.sources)
        assert signal.matched, "必须给出命中的原文，否则用户无法自己判断上下文"
        assert signal.why
        assert signal.action, "每条提示都要有可执行的下一步"


def test_upfront_fee_is_detected_as_warning() -> None:
    report = scan_text("面试通过后需要先交培训费 3000 元")
    signal = next(item for item in report.signals if item.key == "upfront_fee")
    assert signal.severity == "warn"
    assert "培训费" in signal.matched


def test_outsourcing_is_informational_not_a_verdict() -> None:
    report = scan_text("本岗位为外派驻场开发")
    signal = next(item for item in report.signals if item.key == "outsourcing")
    # 外包不是坏事，所以只给 info，并说明它影响什么
    assert signal.severity == "info"
    assert "合同主体" in signal.why or "社保" in signal.why


def test_warnings_come_first() -> None:
    report = scan_text("急招，交押金，大小周")
    severities = [item.severity for item in report.signals]
    assert severities == sorted(severities, key=lambda value: 0 if value == "warn" else 1)


def test_clean_text_has_no_signals_but_still_offers_sources() -> None:
    report = scan_text("负责订单系统的后端开发，要求 3 年以上经验")
    assert report.signals == []
    # 没有信号也要给核实渠道——"没发现问题"不等于"没问题"
    assert report.sources
    assert report.disclaimer


def test_report_never_states_a_conclusion() -> None:
    report = scan_text("交押金 500 元，不签合同")
    assert "不代表" in report.disclaimer or "不代表任何结论" in report.disclaimer
    for signal in report.signals:
        assert "不代表事实" in signal.disclaimer


def test_source_options_shape() -> None:
    options = source_options()
    assert len(options) == len(SOURCES)
    assert all(item["url"].startswith("http") and item["what"] for item in options)


def test_empty_text_is_handled() -> None:
    report = scan_text("")
    assert report.signals == []
    assert report.checked_chars == 0


def test_rules_have_unique_keys() -> None:
    keys = [str(rule["key"]) for rule in RULES]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- #
# 合同提醒
# --------------------------------------------------------------------------- #
def test_contract_disclaimer_says_it_is_not_legal_advice() -> None:
    report = build_report()
    assert "不是法律意见" in report.disclaimer
    assert "律师" in report.disclaimer


def test_every_clause_has_an_askable_question() -> None:
    for item in clause_options():
        # 必须是能直接拿去问 HR 的问句，而不是一条抽象建议
        assert item["question"].endswith("？") or item["question"].endswith("?")
        assert item["why"]
        assert item["trap"], "每条都要提示常见坑，否则就只是通用清单"


def test_checklist_works_without_contract_text() -> None:
    report = build_report("")
    assert len(report.checklist) == len(CLAUSES)
    assert report.mentioned == []
    assert len(report.missing) == len(CLAUSES)


def test_mentioned_clauses_are_detected() -> None:
    report = build_report("试用期 3 个月，年终奖发放时需在职，并有竞业限制条款")
    labels = [hit.clause.label for hit in report.mentioned]
    assert "试用期时长与待遇" in labels
    assert "薪资构成与发放条件" in labels
    assert "竞业限制" in labels


def test_must_ask_comes_first() -> None:
    report = build_report()
    severities = [hit.clause.severity for hit in report.checklist]
    assert severities == sorted(severities, key=lambda value: 0 if value == "must_ask" else 1)
    assert severities[0] == "must_ask"


def test_missing_separates_from_mentioned() -> None:
    report = build_report("有竞业限制，服务期两年")
    mentioned = {hit.clause.key for hit in report.mentioned}
    missing = {hit.clause.key for hit in report.missing}
    assert mentioned & missing == set()
    assert mentioned | missing == {clause.key for clause in CLAUSES}


def test_checked_chars_reported() -> None:
    report = build_report("试用期三个月")
    assert report.checked_chars > 0
