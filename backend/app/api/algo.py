"""算法题白板 API（M13）。

`POST /api/algo/attempts` 用 POST 而不是 GET：它会把作答落库、并调用一次 LLM
做评估，有副作用也有成本。

判题**不在这里**——浏览器端沙箱跑完把结果回传（PLAN §9.2）。服务端只负责
把结果与题库对齐、然后评「思路—取舍—边界」。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..algo_service import (
    AlgoError,
    AlgoNotFoundError,
    attempt_out,
    get_attempt,
    list_attempts,
    problem_list,
    require_problem,
    submit_attempt,
)
from ..deps import get_llm, get_session
from ..providers.llm.base import LLMClient
from ..schemas.algo import (
    AlgoAttemptIn,
    AlgoAttemptOut,
    AlgoProblemBriefOut,
    AlgoProblemOut,
    AlgoTestCaseOut,
)

router = APIRouter(prefix="/api/algo", tags=["algo"])


def _brief(problem) -> AlgoProblemBriefOut:  # noqa: ANN001
    return AlgoProblemBriefOut(
        slug=problem.slug,
        title=problem.title,
        difficulty=problem.difficulty,
        tags=list(problem.tags),
        statement=problem.statement,
        track=problem.track,
        languages=problem.languages,
    )


@router.get("/problems", response_model=list[AlgoProblemBriefOut], summary="算法题清单（按方向可筛）")
def read_problems(
    track: str | None = Query(default=None, description="general | vision"),
) -> list[AlgoProblemBriefOut]:
    return [_brief(problem) for problem in problem_list(track)]


@router.get("/problems/{slug}", response_model=AlgoProblemOut, summary="题目详情：各语言起始代码、测试用例、分级提示")
def read_problem(slug: str) -> AlgoProblemOut:
    try:
        problem = require_problem(slug)
    except AlgoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return AlgoProblemOut(
        **_brief(problem).model_dump(),
        function_names=dict(problem.function_names),
        signatures=dict(problem.signatures),
        starters=dict(problem.starters),
        tests=[
            AlgoTestCaseOut(index=index, args=test.args, expected=test.expected, note=test.note)
            for index, test in enumerate(problem.tests)
        ],
        hints=list(problem.hints),
    )


@router.post(
    "/attempts",
    response_model=AlgoAttemptOut,
    summary="提交作答：落库判题结果并给出「思路—取舍—边界」评估",
)
async def create_attempt(
    payload: AlgoAttemptIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> AlgoAttemptOut:
    try:
        attempt = await submit_attempt(
            db,
            llm=llm,
            problem_slug=payload.problem_slug,
            language=payload.language,
            code=payload.code,
            reported=[case.model_dump() for case in payload.cases],
            duration_ms=payload.duration_ms,
            target_id=payload.target_id,
        )
    except AlgoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlgoError as exc:
        # 语言不支持、用例下标越界：这是调用方的问题，不是「找不到」
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AlgoAttemptOut(**attempt_out(attempt))  # type: ignore[arg-type]


@router.get("/attempts", response_model=list[AlgoAttemptOut], summary="历史作答（可按题目筛选）")
def read_attempts(
    slug: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> list[AlgoAttemptOut]:
    return [
        AlgoAttemptOut(**attempt_out(attempt))  # type: ignore[arg-type]
        for attempt in list_attempts(db, slug=slug, limit=limit)
    ]


@router.get("/attempts/{attempt_id}", response_model=AlgoAttemptOut, summary="单次作答详情")
def read_attempt(attempt_id: int, db: Session = Depends(get_session)) -> AlgoAttemptOut:
    attempt = get_attempt(db, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail=f"没有这次作答：{attempt_id}")
    return AlgoAttemptOut(**attempt_out(attempt))  # type: ignore[arg-type]
