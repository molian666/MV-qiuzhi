"""算法题白板的编排（M13）。

这里有一条重要的信任边界：**用例的期望值是后端从题库填的，不是前端传的**。
前端只回传「哪一例过了、实际输出是什么、报了什么错」。这样评估所依据的事实
全部来自后端可控的题库，前端就算传错也不会把评估带偏。

沙箱可能中断（超时、崩溃），那时前端只跑了前几例——所以**没被回传的用例
标记为未执行**，而不是当作通过。这个区别在评估里很重要：没跑过 ≠ 通过。
"""

from __future__ import annotations

from sqlmodel import Session, select

from .algo import problems as problem_bank
from .algo.problems import AlgoProblem
from .algo.review import CaseOutcome, review_attempt
from .models.algo import AlgoAttempt
from .providers.llm.base import LLMClient


class AlgoError(RuntimeError):
    """业务错误：语言不支持、回传的用例下标越界（→ 400）。"""


class AlgoNotFoundError(AlgoError):
    """题目不存在（→ 404）。与上面分开，否则「语言错了」会被报成「题目不存在」。"""


def problem_list(track: str | None = None) -> tuple[AlgoProblem, ...]:
    return problem_bank.list_problems(track)


def require_problem(slug: str) -> AlgoProblem:
    problem = problem_bank.get_problem(slug)
    if problem is None:
        raise AlgoNotFoundError(f"没有这道题：{slug}")
    return problem


def require_language(problem: AlgoProblem, language: str) -> str:
    """校验语言在题目支持范围内。

    不校验的话，用户拿 JS 题去跑 Python 起始代码只会得到一句
    「找不到函数」——那个错比「这道题只支持 javascript」难懂得多。
    """
    if language not in problem.starters:
        supported = "、".join(problem.languages)
        raise AlgoError(f"「{problem.title}」只支持 {supported}，不支持 {language}")
    return language


def build_case_outcomes(
    problem: AlgoProblem,
    reported: list[dict[str, object]],
) -> list[CaseOutcome]:
    """把前端回传的结果与题库对齐。

    - `note` / `expected` 一律取自题库，不用前端传的值；
    - 下标越界视为调用方 bug，直接报错而不是静默丢弃；
    - 题库里有、前端没回传的用例标为「未执行」。
    """
    total = len(problem.tests)
    by_index: dict[int, dict[str, object]] = {}
    for item in reported:
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < total:
            raise AlgoError(f"用例下标越界：{index}（本题共 {total} 例）")
        by_index[index] = item

    outcomes: list[CaseOutcome] = []
    for index, test in enumerate(problem.tests):
        item = by_index.get(index)
        if item is None:
            outcomes.append(
                CaseOutcome(
                    index=index,
                    passed=False,
                    note=test.note,
                    expected=test.expected,
                    error="未执行（沙箱中断或未跑完）",
                )
            )
            continue
        outcomes.append(
            CaseOutcome(
                index=index,
                passed=bool(item.get("passed")),
                note=test.note,
                expected=test.expected,
                actual=item.get("actual"),
                error=str(item.get("error") or ""),
            )
        )
    return outcomes


async def submit_attempt(
    db: Session,
    *,
    llm: LLMClient,
    problem_slug: str,
    language: str,
    code: str,
    reported: list[dict[str, object]],
    duration_ms: int | None = None,
    target_id: int | None = None,
) -> AlgoAttempt:
    problem = require_problem(problem_slug)
    require_language(problem, language)
    cases = build_case_outcomes(problem, reported)

    review = await review_attempt(
        llm,
        problem=problem,
        language=language,
        code=code,
        cases=cases,
        duration_ms=duration_ms,
    )

    passed = sum(1 for case in cases if case.passed)
    attempt = AlgoAttempt(
        problem_slug=problem.slug,
        language=language,
        code=code,
        passed=passed,
        total=len(cases),
        duration_ms=duration_ms,
        test_report=review_report(cases),
        review=review.model_dump(),
        approach_verdict=review.approach_verdict,
        summary=review.summary,
        target_id=target_id,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def review_report(cases: list[CaseOutcome]) -> dict[str, object]:
    """落库的判题明细（评估之后再存一份，方便前端直接渲染测试结果）。"""
    return {
        "passed": sum(1 for case in cases if case.passed),
        "total": len(cases),
        "cases": [
            {
                "index": case.index,
                "passed": case.passed,
                "note": case.note,
                "expected": case.expected,
                "actual": case.actual,
                "error": case.error,
            }
            for case in cases
        ],
    }


def list_attempts(
    db: Session,
    *,
    slug: str | None = None,
    limit: int = 50,
) -> list[AlgoAttempt]:
    statement = select(AlgoAttempt).order_by(AlgoAttempt.created_at.desc())  # type: ignore[attr-defined]
    if slug:
        statement = statement.where(AlgoAttempt.problem_slug == slug)
    return list(db.exec(statement.limit(limit)).all())


def get_attempt(db: Session, attempt_id: int) -> AlgoAttempt | None:
    return db.get(AlgoAttempt, attempt_id)


def attempt_out(attempt: AlgoAttempt) -> dict[str, object]:
    """补齐 `problem_title`：题库是代码常量，历史记录里的 slug 可能已经不存在。"""
    problem = problem_bank.get_problem(attempt.problem_slug)
    return {
        "id": attempt.id,
        "created_at": attempt.created_at,
        "problem_slug": attempt.problem_slug,
        "problem_title": problem.title if problem else attempt.problem_slug,
        "language": attempt.language,
        "code": attempt.code,
        "passed": attempt.passed,
        "total": attempt.total,
        "duration_ms": attempt.duration_ms,
        "test_report": attempt.test_report,
        "review": attempt.review,
        "approach_verdict": attempt.approach_verdict,
        "summary": attempt.summary,
    }
