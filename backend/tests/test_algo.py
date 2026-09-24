"""算法题白板测试（M13）。

重点不在「接口能返回 200」，而在几条约束：
- 评分参照（key_points）不能泄漏给用户；
- 判题事实必须来自题库，而不是前端回传；
- 评估失败不能吞掉判题结果；
- 模型说法与测试事实矛盾时必须显式指出。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.algo import problems as problem_bank
from app.algo.review import (
    CaseOutcome,
    build_input_payload,
    parse_review,
)
from app.algo_service import AlgoError, build_case_outcomes, require_problem

# --------------------------------------------------------------------------- #
# 题库本身的约束
# --------------------------------------------------------------------------- #


def test_problem_slugs_are_unique() -> None:
    slugs = [problem.slug for problem in problem_bank.list_problems()]
    assert len(slugs) == len(set(slugs))


def test_every_problem_is_usable() -> None:
    for problem in problem_bank.list_problems():
        assert problem.tests, f"{problem.slug} 没有测试用例"
        assert "javascript" in problem.starters, f"{problem.slug} 没有起始代码"
        assert problem.starters["javascript"].strip(), f"{problem.slug} 的起始代码是空的"
        assert problem.hints, f"{problem.slug} 没有提示"
        assert problem.key_points, f"{problem.slug} 没有评分参照"
        assert problem.statement.strip(), f"{problem.slug} 没有题干"


def test_starter_code_declares_the_expected_function() -> None:
    """起始代码里得已经有那个函数名，否则用户不知道该实现什么。"""
    for problem in problem_bank.list_problems():
        assert problem.function_name in problem.starters["javascript"], problem.slug


def test_every_problem_has_enough_cases_to_judge_edges() -> None:
    """M13 验收的是「边界」能力——题库自己先得给够边界用例。"""
    for problem in problem_bank.list_problems():
        assert len(problem.tests) >= 4, f"{problem.slug} 用例太少，评不出边界能力"


def test_expected_values_are_matched_case_by_case() -> None:
    """每道题的用例不该出现「同一个输入对应两个期望值」的矛盾。"""
    for problem in problem_bank.list_problems():
        seen: dict[str, object] = {}
        for test in problem.tests:
            key = json.dumps(test.args, ensure_ascii=False, sort_keys=True)
            if key in seen:
                assert seen[key] == test.expected, f"{problem.slug} 同一输入有两个期望值"
            seen[key] = test.expected


# --------------------------------------------------------------------------- #
# 判题事实与题库对齐
# --------------------------------------------------------------------------- #


def test_expected_values_come_from_the_bank_not_the_caller() -> None:
    """前端传什么 expected / note 都不算数——事实来源必须是题库。"""
    problem = require_problem("two-sum")
    outcomes = build_case_outcomes(
        problem,
        [{"index": 0, "passed": True, "expected": "客户端编的", "note": "客户端编的"}],
    )
    assert outcomes[0].expected == problem.tests[0].expected
    assert outcomes[0].note == problem.tests[0].note


def test_unreported_cases_are_marked_as_not_run() -> None:
    """沙箱可能中途中断。没跑过 ≠ 通过，这个区别在评估里很关键。"""
    problem = require_problem("two-sum")
    outcomes = build_case_outcomes(problem, [{"index": 0, "passed": True}])

    assert len(outcomes) == len(problem.tests)
    assert outcomes[0].passed is True
    assert outcomes[1].passed is False
    assert "未执行" in outcomes[1].error


def test_out_of_range_index_is_rejected_loudly() -> None:
    problem = require_problem("two-sum")
    with pytest.raises(AlgoError):
        build_case_outcomes(problem, [{"index": 99, "passed": True}])


def test_unknown_problem_is_rejected() -> None:
    with pytest.raises(AlgoError):
        require_problem("no-such-problem")


# --------------------------------------------------------------------------- #
# 评估解析：不编造
# --------------------------------------------------------------------------- #


def _cases(all_passed: bool = True) -> list[CaseOutcome]:
    return [
        CaseOutcome(index=0, passed=all_passed, note="示例", expected=1, actual=1),
        CaseOutcome(index=1, passed=True, note="边界", expected=2, actual=2),
    ]


def test_parse_review_reads_a_well_formed_reply() -> None:
    text = json.dumps(
        {
            "approach_summary": "用哈希表把查找降到 O(1)。",
            "approach_verdict": "correct",
            "tradeoffs": [
                {"point": "空间换时间", "assessment": "合理", "kind": "measured"},
            ],
            "edge_cases": [
                {"case": "空数组", "status": "covered", "detail": "测试覆盖"},
            ],
            "complexity": {"time": "O(n)", "space": "O(n)", "assessment": "可以"},
            "risks": [],
            "followups": ["如果要求返回所有解呢？"],
            "summary": "方向对，边界清楚。",
        },
        ensure_ascii=False,
    )
    review = parse_review(text, cases=_cases())

    assert review.approach_verdict == "correct"
    assert review.tradeoffs[0].kind == "measured"
    assert review.edge_cases[0].status == "covered"
    assert review.complexity["time"] == "O(n)"
    assert review.notes == ""


def test_parse_review_degrades_instead_of_raising() -> None:
    """评估失败不该让整次提交丢掉——判题结果已经拿到了。"""
    review = parse_review("模型今天不想输出 JSON", cases=_cases())

    assert review.approach_verdict == "unknown"
    assert "未生成" in review.notes


def test_missing_kind_defaults_to_inferred() -> None:
    """没有证据时宁可说得保守。"""
    text = json.dumps(
        {"tradeoffs": [{"point": "复杂度", "assessment": "可能偏慢"}]},
        ensure_ascii=False,
    )
    review = parse_review(text, cases=_cases())

    assert review.tradeoffs[0].kind == "inferred"


def test_unknown_enum_values_fall_back() -> None:
    text = json.dumps(
        {
            "approach_verdict": "完美",
            "edge_cases": [{"case": "空数组", "status": "大概吧"}],
            "tradeoffs": [{"point": "x", "assessment": "y", "kind": "猜的"}],
        },
        ensure_ascii=False,
    )
    review = parse_review(text, cases=_cases())

    assert review.approach_verdict == "unknown"
    assert review.edge_cases[0].status == "untested"
    assert review.tradeoffs[0].kind == "inferred"


def test_contradiction_between_model_and_tests_is_flagged() -> None:
    """测试有失败用例，模型却说思路正确 → 必须把矛盾摆在明面上。"""
    review = parse_review(
        json.dumps({"approach_verdict": "correct", "summary": "没问题"}, ensure_ascii=False),
        cases=[CaseOutcome(index=0, passed=False, note="边界", error="返回 undefined")],
    )
    assert "不一致" in review.notes


def test_no_contradiction_note_when_tests_pass() -> None:
    review = parse_review(
        json.dumps({"approach_verdict": "correct"}, ensure_ascii=False),
        cases=_cases(),
    )
    assert review.notes == ""


# --------------------------------------------------------------------------- #
# 评估输入：模型必须拿到「这道题希望谈到什么」
# --------------------------------------------------------------------------- #


def test_input_payload_carries_expected_discussion_points() -> None:
    problem = require_problem("two-sum")
    payload = build_input_payload(
        problem, language="javascript", code="function twoSum(){}", cases=_cases()
    )

    points = payload["problem"]["expected_discussion_points"]
    assert points == list(problem.key_points)
    assert payload["test_result"]["passed"] == 2
    assert payload["test_result"]["total"] == 2


def test_input_payload_reports_failures_with_their_error() -> None:
    problem = require_problem("two-sum")
    payload = build_input_payload(
        problem,
        language="javascript",
        code="function twoSum(){}",
        cases=[CaseOutcome(index=0, passed=False, note="示例", error="TypeError: x is not a function")],
    )

    case = payload["test_result"]["cases"][0]
    assert case["passed"] is False
    assert "TypeError" in case["error"]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_list_problems(client: TestClient) -> None:
    response = client.get("/api/algo/problems")
    assert response.status_code == 200

    body = response.json()
    assert len(body) == len(problem_bank.list_problems())
    assert {item["slug"] for item in body} == {p.slug for p in problem_bank.list_problems()}


def test_problem_detail_includes_tests_and_starters(client: TestClient) -> None:
    body = client.get("/api/algo/problems/two-sum").json()

    assert body["function_name"] == "twoSum"
    assert len(body["tests"]) >= 4
    assert "javascript" in body["starters"]
    assert body["tests"][0]["index"] == 0
    assert "expected" in body["tests"][0]


def test_problem_detail_does_not_leak_scoring_reference(client: TestClient) -> None:
    """评分参照（这道题希望被谈到什么）不能提前看到，否则就变成抄答案。"""
    response = client.get("/api/algo/problems/two-sum")
    assert "key_points" not in response.text
    assert "expected_discussion_points" not in response.text


def test_problem_detail_404(client: TestClient) -> None:
    response = client.get("/api/algo/problems/not-a-real-problem")
    assert response.status_code == 404


def _submit(client: TestClient, slug: str, *, passed: list[bool]) -> dict:
    problem = require_problem(slug)
    code = problem.starters["javascript"].replace("// 在这里写你的实现", "").strip()
    response = client.post(
        "/api/algo/attempts",
        json={
            "problem_slug": slug,
            "language": "javascript",
            "code": code,
            "duration_ms": 1234,
            "cases": [
                {"index": index, "passed": ok, "actual": "x" if ok else "y"}
                for index, ok in enumerate(passed)
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_submit_attempt_records_result_and_review(client: TestClient) -> None:
    problem = require_problem("two-sum")
    attempt = _submit(client, "two-sum", passed=[True] * len(problem.tests))

    assert attempt["problem_slug"] == "two-sum"
    assert attempt["problem_title"] == problem.title
    assert attempt["passed"] == len(problem.tests)
    assert attempt["total"] == len(problem.tests)
    assert attempt["test_report"]["passed"] == attempt["passed"]
    assert attempt["approach_verdict"] == "correct"  # 全过，Mock 也判 correct


def test_submit_attempt_exposes_the_three_dimensions(client: TestClient) -> None:
    """M13 的验收标准：思路 / 取舍 / 边界三个维度都要评到，而不是只报对错。"""
    problem = require_problem("two-sum")
    attempt = _submit(client, "two-sum", passed=[True, False] + [True] * (len(problem.tests) - 2))

    review = attempt["review"]
    assert review["approach_summary"], "缺「思路」"
    assert review["tradeoffs"], "缺「取舍」"
    assert review["edge_cases"], "缺「边界」"
    assert attempt["approach_verdict"] == "partial"


def test_submit_attempt_marks_the_failed_case(client: TestClient) -> None:
    problem = require_problem("two-sum")
    attempt = _submit(client, "two-sum", passed=[False] * len(problem.tests))

    assert attempt["approach_verdict"] == "wrong"
    failed = [case for case in attempt["test_report"]["cases"] if not case["passed"]]
    assert len(failed) == len(problem.tests)
    assert attempt["review"]["risks"], "失败了一定要有风险提示"


def test_submit_rejects_unknown_problem(client: TestClient) -> None:
    response = client.post(
        "/api/algo/attempts",
        json={"problem_slug": "nope", "code": "x", "cases": []},
    )
    assert response.status_code == 404


def test_submit_rejects_out_of_range_case(client: TestClient) -> None:
    response = client.post(
        "/api/algo/attempts",
        json={
            "problem_slug": "two-sum",
            "code": "x",
            "cases": [{"index": 500, "passed": True}],
        },
    )
    assert response.status_code == 404


def test_attempts_are_listed_and_filterable(client: TestClient) -> None:
    _submit(client, "two-sum", passed=[True] * len(require_problem("two-sum").tests))
    _submit(client, "binary-search", passed=[True, False])

    everything = client.get("/api/algo/attempts").json()
    assert len(everything) == 2

    filtered = client.get("/api/algo/attempts", params={"slug": "binary-search"}).json()
    assert len(filtered) == 1
    assert filtered[0]["problem_slug"] == "binary-search"


def test_attempt_detail_round_trips(client: TestClient) -> None:
    problem = require_problem("binary-search")
    created = _submit(client, "binary-search", passed=[True] * len(problem.tests))

    fetched = client.get(f"/api/algo/attempts/{created['id']}").json()
    assert fetched["id"] == created["id"]
    assert fetched["code"] == created["code"]
    assert fetched["review"] == created["review"]


def test_attempt_detail_404(client: TestClient) -> None:
    assert client.get("/api/algo/attempts/99999").status_code == 404
