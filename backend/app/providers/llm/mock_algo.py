"""Mock 的算法题评估：没有 Key 也能跑通 M13 闭环。

不是随机字符串——它**从真实事实生成**：测试哪几例过了、代码里有没有嵌套循环、
题目希望谈到哪些方向。所以 Mock 输出的结构、字段取值范围与真实模型一致，
前端可以按同一套渲染逻辑开发，不会被真实模型一开就崩。

按项目惯例，`notes` 里明确标注这是 Mock 评估，不假装成真实模型的判断。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...algo.review import ALGO_INPUT_MARKER_END, ALGO_INPUT_MARKER_START
from .base import LLMMessage

_LOOP_KEYWORDS = ("for", "while", "forEach", "map(", "reduce(")


def extract_algo_payload(messages: list[LLMMessage]) -> dict[str, Any] | None:
    """从对话里取回结构化输入（与真实模型读的是同一段）。"""
    for message in messages:
        start = message.content.find(ALGO_INPUT_MARKER_START)
        end = message.content.find(ALGO_INPUT_MARKER_END)
        if start == -1 or end == -1 or end <= start:
            continue
        body = message.content[start + len(ALGO_INPUT_MARKER_START) : end].strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def build_mock_algo_review(messages: list[LLMMessage]) -> str:
    payload = extract_algo_payload(messages)
    if payload is None:
        return json.dumps(
            {
                "approach_summary": "",
                "approach_verdict": "unknown",
                "tradeoffs": [],
                "edge_cases": [],
                "complexity": {},
                "risks": [],
                "followups": [],
                "summary": "Mock 评估：没读到结构化输入，无法给出判断。",
            },
            ensure_ascii=False,
        )

    problem: dict[str, Any] = payload.get("problem") or {}
    result: dict[str, Any] = payload.get("test_result") or {}
    code: str = payload.get("code") or ""
    cases: list[dict[str, Any]] = result.get("cases") or []

    passed = int(result.get("passed") or 0)
    total = int(result.get("total") or 0)
    failed = [case for case in cases if not case.get("passed")]

    # 判定严格跟着测试事实走，不做主观发挥——Mock 不该比真实模型更会"猜"
    if total == 0:
        verdict = "unknown"
    elif passed == total:
        verdict = "correct"
    elif passed == 0:
        verdict = "wrong"
    else:
        verdict = "partial"

    return json.dumps(
        {
            "approach_summary": (
                f"Mock 评估：{total} 个用例中通过 {passed} 个。"
                f"代码 {len(code.splitlines())} 行。"
            ),
            "approach_verdict": verdict,
            "tradeoffs": _mock_tradeoffs(code, passed, total),
            "edge_cases": _mock_edge_cases(cases),
            "complexity": _mock_complexity(code),
            "risks": _mock_risks(failed),
            "followups": _mock_followups(problem, failed),
            "summary": (
                f"Mock 评估（未配置真实模型，结论仅供链路验证）："
                f"{total} 例中通过 {passed} 例，判定为 {verdict}。"
                "思路、取舍、边界的细致评价需要真实模型。"
            ),
        },
        ensure_ascii=False,
    )


def _mock_tradeoffs(code: str, passed: int, total: int) -> list[dict[str, str]]:
    loops = _count_loops(code)
    items: list[dict[str, str]] = [
        {
            "point": f"测试覆盖度：{passed}/{total}",
            "assessment": "来自测试结果，是实测事实。",
            "kind": "measured",
        }
    ]
    if loops == 0:
        items.append(
            {
                "point": "代码里没有可见的循环",
                "assessment": "若题目需要遍历输入，这通常意味着实现不完整；此处是静态推断，未实测。",
                "kind": "inferred",
            }
        )
    elif loops == 1:
        items.append(
            {
                "point": "单层循环",
                "assessment": "线性扫描的形态，时间复杂度大概率是 O(n) 量级；未实测运行时间。",
                "kind": "inferred",
            }
        )
    else:
        items.append(
            {
                "point": f"检测到约 {loops} 处循环语句",
                "assessment": "可能存在嵌套，需要自己确认时间复杂度；这是静态计数，不是实测。",
                "kind": "inferred",
            }
        )
    return items


def _mock_edge_cases(cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for case in cases:
        note = str(case.get("note") or f"用例 {case.get('index')}")
        ok = bool(case.get("passed"))
        out.append(
            {
                "case": note,
                "status": "covered" if ok else "missed",
                "detail": "测试通过（实测）" if ok else f"测试失败（实测）：{case.get('error') or '结果不符'}",
            }
        )
    return out


def _mock_complexity(code: str) -> dict[str, str]:
    loops = _count_loops(code)
    guess = "O(1)" if loops == 0 else ("O(n)" if loops == 1 else "可能 O(n²) 或更高")
    return {
        "time": guess,
        "space": "未判断",
        "assessment": "Mock 只按循环语句数量粗略估计，未做运行时间实测，不能作为结论。",
    }


def _mock_risks(failed: list[dict[str, Any]]) -> list[str]:
    if not failed:
        return []
    notes = [str(case.get("note") or f"用例 {case.get('index')}") for case in failed]
    return [f"未通过的用例：{note}" for note in notes]


def _mock_followups(problem: dict[str, Any], failed: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    if failed:
        out.append(f"先看没过的这例：{failed[0].get('note') or failed[0].get('index')}。问题出在哪一步？")
    points = problem.get("expected_discussion_points") or []
    for point in points[:2]:
        out.append(f"围绕这一点展开：{point}")
    return out


def _count_loops(code: str) -> int:
    """粗略数一下循环语句。故意**不**追求准确——它只用于 Mock 的提示性说明，
    而且输出里标了 `inferred`。真实模型看的是完整代码。"""
    stripped = _strip_strings_and_comments(code)
    return sum(len(re.findall(rf"\b{re.escape(word)}", stripped)) for word in _LOOP_KEYWORDS)


def _strip_strings_and_comments(code: str) -> str:
    code = re.sub(r"//[^\n]*", " ", code)
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.DOTALL)
    code = re.sub(r"'[^'\n]*'", " ", code)
    code = re.sub(r'"[^"\n]*"', " ", code)
    return re.sub(r"`[^`]*`", " ", code, flags=re.DOTALL)
