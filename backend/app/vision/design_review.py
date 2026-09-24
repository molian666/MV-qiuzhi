"""系统设计作答的评估（M13）。

与算法题评估的核心差别：**系统设计没有唯一正确答案**，所以这里不判对错，
而是按题目给的 `rubric` 逐维度看「候选人提到了什么、漏了什么」。

一条最要紧的约束：

> **候选人没提到的，就说「未提及」，不要替他补上再夸他想到了。**

大模型在这类任务上有个顽固倾向：知道标准答案之后，会不自觉地把答案里的要点
算到候选人头上。这会让评估变成安慰——而这个系统存在的意义正好相反。
所以输出里每个维度都要分开列 `mentioned` 与 `missing`，
并且每一条都要标 `kind`（`stated` 候选人明确说了 / `inferred` 从他的话推断）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from ..providers.llm.base import LLMClient, LLMMessage, extract_json
from ..schemas.design import DesignDimensionOut, DesignReviewOut
from .design import DesignQuestion, RubricDimension

#: 每个 LLM 调用都要带 task 标签（PLAN §9.1 第 5 条）
TASK = "design_review"

DESIGN_INPUT_MARKER_START = "【系统设计评估输入 JSON】"
DESIGN_INPUT_MARKER_END = "【输入结束】"

_SCORES = ("strong", "ok", "weak", "missing")
_KINDS = ("stated", "inferred")

_SYSTEM = """你是一位机器视觉方向的技术负责人，正在评估候选人对一道**系统设计题**的作答。

系统设计题**没有唯一正确答案**。你要评的不是「像不像标准答案」，而是这四件事：

1. **需求澄清**：有没有先问清约束就开始设计——这是系统设计最常见的失分点；
2. **整体架构**：数据流与模块划分是否讲清楚了；
3. **关键取舍**：延迟/吞吐、精度/速度、成本/可维护性之间的权衡有没有意识到；
4. **边界与故障**：异常处理和长期维护有没有考虑。

最重要的硬性要求（违反这条，整个评估就没价值了）：

- **候选人没提到的，一律写进 `missing`，绝不要替他补上再算作他说到了。**
  你知道标准答案，会不自觉地把答案里的要点算到他头上——必须克制这一点。
- 每条 `mentioned` 都要标 `kind`：`stated`（候选人明确说了）或
  `inferred`（你从他的表述推断出来的）。分不清就归到 `inferred`。
- 没提到的维度，`score` 填 `missing`，不要用「部分完成」含糊过去。

其他要求：

- 不要给通用建议（「建议多考虑异常情况」这种），要说**这道题具体漏了哪一块**；
- 不要评价候选人的表达能力，只看内容；
- 全部用中文。"""

_OUTPUT_SHAPE = """只输出一个 JSON 对象，不要解释文字、不要 markdown 代码块：

{
  "dimensions": [
    {
      "dimension": "维度名（与题目给的维度一致）",
      "score": "strong | ok | weak | missing",
      "assessment": "这个维度上候选人的表现，具体一点",
      "mentioned": [{"point": "他说到的要点", "kind": "stated | inferred"}],
      "missing": ["这个维度里他没提到的要点"]
    }
  ],
  "strengths": ["真正做得好的地方，2-3 条"],
  "gaps": ["最关键的遗漏，按重要性排序，2-4 条"],
  "followups": ["面试官会追问的问题，针对这份作答，3-4 个"],
  "summary": "一段总评：作为技术负责人的判断"
}"""


def extract_scene_labels(scene_json: str) -> list[str]:
    """从 Excalidraw 场景里抽出文字标签。

    候选人在画布上写的模块名、箭头标注就是方案的一部分，白白丢掉很可惜。
    抽不出来就当作没有画布内容，不影响评估——文字说明才是主依据。
    """
    if not scene_json.strip():
        return []

    try:
        payload = json.loads(scene_json)
    except json.JSONDecodeError:
        return []

    elements = payload.get("elements") if isinstance(payload, dict) else payload
    if not isinstance(elements, list):
        return []

    labels: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("type") != "text":
            continue
        text = str(element.get("text") or "").strip()
        # 去掉 Excalidraw 的换行转义与首尾空白，太长的截断
        text = re.sub(r"\s+", " ", text).strip()
        if text and text not in labels:
            labels.append(text[:200])
    return labels[:120]


def _rubric_payload(question: DesignQuestion) -> list[dict[str, Any]]:
    return [
        {
            "dimension": item.dimension,
            "weight": item.weight,
            "expects": list(item.expects),
        }
        for item in question.rubric
    ]


def build_input_payload(
    question: DesignQuestion,
    *,
    writeup: str,
    scene_labels: Sequence[str],
) -> dict[str, Any]:
    """打包评估所需的全部事实。Mock 与真实模型共用这一份约定。"""
    return {
        "question": {
            "title": question.title,
            "statement": question.statement,
            "constraints": list(question.constraints),
            "open_questions": list(question.open_questions),
        },
        "rubric": _rubric_payload(question),
        "candidate": {
            "writeup": writeup,
            # 画布上的文字标签：他说「画了什么」也是方案的一部分
            "whiteboard_labels": list(scene_labels),
        },
    }


def build_review_messages(payload: dict[str, Any]) -> list[LLMMessage]:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(
            role="user",
            content=(
                f"{DESIGN_INPUT_MARKER_START}\n{body}\n{DESIGN_INPUT_MARKER_END}\n\n"
                f"请按下面的结构给出评估。注意：`rubric` 里的 `expects` 是给你判断用的，"
                f"**候选人没提到的必须写进 missing**。\n\n{_OUTPUT_SHAPE}"
            ),
        ),
    ]


async def review_design(
    llm: LLMClient,
    *,
    question: DesignQuestion,
    writeup: str,
    scene_labels: Sequence[str],
) -> DesignReviewOut:
    payload = build_input_payload(question, writeup=writeup, scene_labels=scene_labels)
    result = await llm.complete(
        task=TASK,
        messages=build_review_messages(payload),
        temperature=0.3,
        json_object=True,
    )
    return parse_design_review(result.text, question=question)


def parse_design_review(text: str, *, question: DesignQuestion) -> DesignReviewOut:
    """解析模型输出。解析失败降级成带说明的空评估，不抛异常——
    作答已经存下来了，评估失败不该让它丢掉。"""
    try:
        raw = extract_json(text)
    except ValueError:
        return DesignReviewOut(notes="评估未生成：模型输出不是可解析的 JSON。作答已保存。")

    dimensions = _dimensions(raw.get("dimensions"), question)
    review = DesignReviewOut(
        dimensions=dimensions,
        strengths=_strings(raw.get("strengths")),
        gaps=_strings(raw.get("gaps")),
        followups=_strings(raw.get("followups")),
        summary=_as_text(raw.get("summary")),
    )
    review.verdict = _verdict(dimensions)
    review.notes = _coverage_note(dimensions)
    return review


def _dimensions(value: Any, question: DesignQuestion) -> list[DesignDimensionOut]:
    """以题目给的 rubric 维度为准，模型少给的补空、多给的丢弃。

    这样界面上永远是「这几个维度各是什么表现」，不会因为模型漏了一维就少一块。
    """
    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = _as_text(item.get("dimension"))
                if name:
                    by_name[name] = item

    out: list[DesignDimensionOut] = []
    for dimension in question.rubric:
        item = by_name.get(dimension.dimension)
        if item is None:
            out.append(
                DesignDimensionOut(
                    dimension=dimension.dimension,
                    weight=dimension.weight,
                    score="missing",
                    assessment="模型没有对这一维给出判断。",
                )
            )
            continue
        out.append(
            DesignDimensionOut(
                dimension=dimension.dimension,
                weight=dimension.weight,
                score=_pick(item.get("score"), _SCORES, "ok"),
                assessment=_as_text(item.get("assessment")),
                mentioned=_mentions(item.get("mentioned")),
                missing=_strings(item.get("missing")),
            )
        )
    return out


def _mentions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            # 模型有时直接给字符串数组，按 stated 处理（保守起见其实是 inferred，
            # 但它既然写成一句陈述，说明它认为候选人说了）
            if item.strip():
                out.append({"point": item.strip(), "kind": "stated"})
            continue
        if not isinstance(item, dict):
            continue
        point = _as_text(item.get("point"))
        if point:
            out.append({"point": point, "kind": _pick(item.get("kind"), _KINDS, "inferred")})
    return out


def _verdict(dimensions: list[DesignDimensionOut]) -> str:
    """总判定：看核心维度（core）有没有到位的。

    `strong` 需要所有核心维度都 strong 或 ok 且至少一个 strong；
    有核心维度是 missing/weak 就压到 partial 或 weak。
    """
    core = [item for item in dimensions if item.weight == "core"] or dimensions
    if not core:
        return "unknown"

    scores = [item.score for item in core]
    if all(score in ("strong", "ok") for score in scores):
        return "strong" if "strong" in scores else "ok"
    if any(score in ("strong", "ok") for score in scores):
        return "partial"
    return "weak"


def _coverage_note(dimensions: list[DesignDimensionOut]) -> str:
    """把「覆盖情况」直接说出来，而不是让人自己数。"""
    missing = [item.dimension for item in dimensions if item.score == "missing"]
    if missing:
        return f"以下维度模型判定为未覆盖：{'、'.join(missing)}。以逐维度的明细为准。"
    return ""


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _pick(value: Any, allowed: Sequence[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def rubric_summary(question: DesignQuestion) -> list[RubricDimension]:
    """给前端展示的评分维度（不含 expects——那是评估参照，不该提前给）。"""
    return list(question.rubric)
