"""算法题作答的「思路—取舍—边界」评估（M13）。

M13 的验收标准是「能给出思路—取舍—边界的评估，不只看对错」，所以这里刻意
**不重复判对错**：对错由浏览器沙箱的测试结果给出，是客观的；模型负责的是
测试结果说不出来的部分——方向选得对不对、权衡有没有意识到、边界想到没有。

一条硬约束：输出里的 `kind` 字段必须区分 `measured`（有测试结果直接支撑）与
`inferred`（从代码推断，未实测）。少了这个区分，模型很容易把「可能超时」
写成「会超时」——把可能性说成事实，正是这个项目最要避免的东西。

结构化输入用标记包裹（与 `providers/llm/base.py` 的 RECORD_MARKER 同一套做法）：
Mock 与真实模型共用这份约定，Mock 不必去猜自然语言。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from ..providers.llm.base import LLMClient, LLMMessage, extract_json
from ..schemas.algo import AlgoEdgeCaseOut, AlgoReviewOut, AlgoTradeoffOut
from .problems import AlgoProblem

#: 每个 LLM 调用都要带 task 标签（PLAN §9.1 第 5 条）
TASK = "algo_review"

ALGO_INPUT_MARKER_START = "【算法题评估输入 JSON】"
ALGO_INPUT_MARKER_END = "【输入结束】"

_VERDICTS = ("correct", "partial", "wrong", "unknown")
_EDGE_STATUS = ("covered", "missed", "untested")
_TRADEOFF_KINDS = ("measured", "inferred")

_SYSTEM = """你是一位技术面试官，在评估候选人对一道算法题的作答。

你的任务**不是判对错**——测试结果已经跑出来了，那是客观事实。你要评的是测试结果
说不出来的三件事：

1. **思路**：解法方向对不对，有没有更优的方向，为什么；
2. **取舍**：时间复杂度、空间复杂度、可读性、实现代价之间的权衡，候选人有没有意识到；
3. **边界**：空输入、单元素、重复元素、极值、越界这些情况想到了没有。

硬性要求：

- `tradeoffs[].kind` 必须区分 `measured`（有测试结果直接支撑）与 `inferred`
  （你从代码推断，没有实测）。**不要把推断写成断言**：测试没覆盖的行为，
  说「未验证」而不是「会失败」。
- 不要编造没发生的事。没测过就承认没测过。
- 不要逐行复述代码，只讲判断和理由。
- 不要给通用鸡汤（「多练习」「注意细节」这类），要说这道题具体的事。
- 如果测试全过但解法明显低效，要点出来；如果测试没过但方向对，也要承认。
- 全部用中文。"""

_OUTPUT_SHAPE = """只输出一个 JSON 对象，不要解释文字、不要 markdown 代码块：

{
  "approach_summary": "候选人的解法思路，两三句话",
  "approach_verdict": "correct | partial | wrong",
  "tradeoffs": [
    {"point": "取舍点（如「时间复杂度 O(n²)」）", "assessment": "评价", "kind": "measured | inferred"}
  ],
  "edge_cases": [
    {"case": "边界情况", "status": "covered | missed | untested", "detail": "依据"}
  ],
  "complexity": {"time": "O(?)", "space": "O(?)", "assessment": "评价"},
  "risks": ["可能导致失败或误判的具体风险"],
  "followups": ["面试官会追问的问题（2-3 个，针对这份作答）"],
  "summary": "一段话总评：方向、取舍、边界各占多少分量的判断"
}"""


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """浏览器沙箱回传的单例结果（已归一化，不依赖 API 层的 schema）。"""

    index: int
    passed: bool
    note: str = ""
    expected: Any = None
    actual: Any = None
    error: str = ""


def build_input_payload(
    problem: AlgoProblem,
    *,
    language: str,
    code: str,
    cases: Sequence[CaseOutcome],
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """打包评估所需的**全部事实**。Mock 与真实模型共用这一份约定。"""
    passed = sum(1 for case in cases if case.passed)
    return {
        "problem": {
            "slug": problem.slug,
            "title": problem.title,
            "difficulty": problem.difficulty,
            "statement": problem.statement,
            "signature": problem.signature_for(language),
            "function_name": problem.function_name_for(language),
            # 评分参照：告诉模型「这道题希望被谈到什么」，而不是让它自由发挥
            "expected_discussion_points": list(problem.key_points),
        },
        "language": language,
        "code": code,
        "test_result": {
            "passed": passed,
            "total": len(cases),
            "duration_ms": duration_ms,
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
        },
    }


def build_review_messages(payload: dict[str, Any]) -> list[LLMMessage]:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(
            role="user",
            content=(
                f"{ALGO_INPUT_MARKER_START}\n{body}\n{ALGO_INPUT_MARKER_END}\n\n"
                f"请按下面的结构给出评估：\n\n{_OUTPUT_SHAPE}"
            ),
        ),
    ]


async def review_attempt(
    llm: LLMClient,
    *,
    problem: AlgoProblem,
    language: str,
    code: str,
    cases: Sequence[CaseOutcome],
    duration_ms: int | None = None,
) -> AlgoReviewOut:
    payload = build_input_payload(
        problem, language=language, code=code, cases=cases, duration_ms=duration_ms
    )
    result = await llm.complete(
        task=TASK,
        messages=build_review_messages(payload),
        temperature=0.3,
        json_object=True,
    )
    return parse_review(result.text, cases=cases)


def parse_review(text: str, *, cases: Sequence[CaseOutcome]) -> AlgoReviewOut:
    """解析模型输出。**任何解析失败都降级成带说明的空评估，不抛异常**——
    判题结果已经拿到了，评估失败不该让整次提交丢掉。"""
    try:
        raw = extract_json(text)
    except ValueError:
        return AlgoReviewOut(notes="评估未生成：模型输出不是可解析的 JSON。判题结果不受影响。")

    review = AlgoReviewOut(
        approach_summary=_as_text(raw.get("approach_summary")),
        approach_verdict=_pick(raw.get("approach_verdict"), _VERDICTS, "unknown"),
        tradeoffs=_tradeoffs(raw.get("tradeoffs")),
        edge_cases=_edge_cases(raw.get("edge_cases")),
        complexity=_complexity(raw.get("complexity")),
        risks=_strings(raw.get("risks")),
        followups=_strings(raw.get("followups")),
        summary=_as_text(raw.get("summary")),
    )
    review.notes = _consistency_note(review, cases)
    return review


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _pick(value: Any, allowed: Sequence[str], fallback: str) -> Any:
    return value if isinstance(value, str) and value in allowed else fallback


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _tradeoffs(value: Any) -> list[AlgoTradeoffOut]:
    if not isinstance(value, list):
        return []
    out: list[AlgoTradeoffOut] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        point = _as_text(item.get("point"))
        if not point:
            continue
        out.append(
            AlgoTradeoffOut(
                point=point,
                assessment=_as_text(item.get("assessment")),
                # 缺省按 inferred：没有证据时宁可说得保守
                kind=_pick(item.get("kind"), _TRADEOFF_KINDS, "inferred"),
            )
        )
    return out


def _edge_cases(value: Any) -> list[AlgoEdgeCaseOut]:
    if not isinstance(value, list):
        return []
    out: list[AlgoEdgeCaseOut] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        case = _as_text(item.get("case"))
        if not case:
            continue
        out.append(
            AlgoEdgeCaseOut(
                case=case,
                status=_pick(item.get("status"), _EDGE_STATUS, "untested"),
                detail=_as_text(item.get("detail")),
            )
        )
    return out


def _complexity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("time", "space", "assessment"):
        text = _as_text(value.get(key))
        if text:
            out[key] = text
    return out


def _consistency_note(review: AlgoReviewOut, cases: Sequence[CaseOutcome]) -> str:
    """把「模型的说法」与「测试的事实」对一遍，不一致就写在明面上。

    模型偶尔会在测试全过时说「有用例会失败」。与其相信它，不如把矛盾摆出来，
    由人判断——这正是本项目处理「模型不可靠」的一贯做法。
    """
    failed = [case for case in cases if not case.passed]
    if not failed:
        return ""
    if review.approach_verdict == "correct":
        return "注意：测试存在失败用例，但模型判定思路为 correct，两者不一致，请以测试结果为准。"
    return ""
