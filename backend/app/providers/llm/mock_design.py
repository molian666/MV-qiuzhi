"""Mock 的系统设计评估：没有 Key 也能跑通 M13 的白板闭环。

**它不编造评价。** 只做一件事：把候选人的文字（说明 + 白板标签）与题目的评分维度
逐条对照，能对上的算「提到了」，对不上的算「没提到」。

匹配用 2-gram 的 Jaccard 相似度——粗暴，但**方向是对的**：
它不会替候选人补上他没说的东西，而这正是真实模型最容易犯错的地方。
所有条目标 `inferred`，因为确实是推断出来的。
"""

from __future__ import annotations

import json
from typing import Any

from ...vision.design_review import DESIGN_INPUT_MARKER_END, DESIGN_INPUT_MARKER_START
from .base import LLMMessage

#: 一条期望要点里有多少比例的内容出现在作答里，才算「提到了」。
#: 用覆盖率而不是 Jaccard——理由见 `_coverage`。
_MATCH_THRESHOLD = 0.5


def extract_design_payload(messages: list[LLMMessage]) -> dict[str, Any] | None:
    for message in messages:
        start = message.content.find(DESIGN_INPUT_MARKER_START)
        end = message.content.find(DESIGN_INPUT_MARKER_END)
        if start == -1 or end == -1 or end <= start:
            continue
        body = message.content[start + len(DESIGN_INPUT_MARKER_START) : end].strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(char for char in text if not char.isspace())
    return {cleaned[index : index + 2] for index in range(max(0, len(cleaned) - 1))}


def _coverage(needle: str, haystack: str) -> float:
    """`needle`（一条期望要点）里的内容有多少比例出现在 `haystack`（整段作答）里。

    **不用 Jaccard**：作答通常比单条要点长得多，Jaccard 的分母（并集）会被作答
    主导，于是写得越详细、每条要点越难达标——量纲反了。实测就是这样：
    一段写足了成像细节的作答，被判成「成像方案：未覆盖」。

    用覆盖率就没有这个问题：分母始终是「这条要点本身有多少内容」。
    """
    expected = _bigrams(needle)
    if not expected:
        return 0.0
    return len(expected & _bigrams(haystack)) / len(expected)


def build_mock_design_review(messages: list[LLMMessage]) -> str:
    payload = extract_design_payload(messages)
    if payload is None:
        return json.dumps(
            {
                "dimensions": [],
                "strengths": [],
                "gaps": [],
                "followups": [],
                "summary": "Mock 评估：没读到结构化输入，无法给出判断。",
            },
            ensure_ascii=False,
        )

    candidate = payload.get("candidate") or {}
    writeup = str(candidate.get("writeup") or "")
    labels = candidate.get("whiteboard_labels") or []
    haystack = writeup + "\n" + "\n".join(str(label) for label in labels)

    dimensions: list[dict[str, Any]] = []
    total_missing = 0

    for item in payload.get("rubric") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("dimension") or "")
        expects = [str(text) for text in (item.get("expects") or [])]
        mentioned: list[dict[str, str]] = []
        missing: list[str] = []

        for expect in expects:
            if _coverage(expect, haystack) >= _MATCH_THRESHOLD:
                # 关键词对上了，但具体说没说透 Mock 判断不了——标 inferred
                mentioned.append({"point": expect, "kind": "inferred"})
            else:
                missing.append(expect)

        ratio = len(mentioned) / len(expects) if expects else 0.0
        if ratio >= 0.8:
            score = "strong"
        elif ratio >= 0.5:
            score = "ok"
        elif ratio > 0:
            score = "weak"
        else:
            score = "missing"
            total_missing += 1

        dimensions.append(
            {
                "dimension": name,
                "score": score,
                "assessment": (
                    f"Mock 按关键词比对：{len(mentioned)}/{len(expects)} 条要点在作答里"
                    f"找到了相近表述。这是粗糙的匹配，不能作为真实评价。"
                ),
                "mentioned": mentioned,
                "missing": missing,
            }
        )

    return json.dumps(
        {
            "dimensions": dimensions,
            "strengths": [],
            "gaps": [
                f"以下维度 Mock 没在作答里找到对应内容：{dimension['dimension']}"
                for dimension in dimensions
                if dimension["score"] == "missing"
            ],
            "followups": [],
            "summary": (
                "Mock 评估（未配置真实模型，结论仅供链路验证）："
                f"按关键词比对，{len(dimensions) - total_missing}/{len(dimensions)} 个维度"
                "找到相关表述。系统设计题的细致评价需要真实模型——"
                "尤其「有没有先问清约束」这类判断，关键词匹配是看不出来的。"
            ),
        },
        ensure_ascii=False,
    )
