"""评分链路的纯逻辑：构造问答对、构造 prompt、规范化模型输出。

这一层不碰数据库、不碰网络，因此可以完整单测；
"读库 → 调模型 → 落库"的编排放在 `app/report_service.py`。

规范化（normalize）为什么重要：模型输出的 JSON 是不可信的（缺字段、分数越界、
引用不存在的回合 id、维度 key 拼错都会发生）。报告页和趋势图都建立在这份数据上，
所以必须在入库前收敛成确定的形状，而不是把脏数据留给前端兜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models.session import SessionTurn
from ..providers.llm.base import (
    RECORD_MARKER_END,
    RECORD_MARKER_START,
    LLMMessage,
)
from .glossary import glossary_for_prompt
from .rubric import DIMENSIONS, build_rubric_prompt, is_valid_dimension, label_for

SCORING_SYSTEM_PROMPT = """你是一位严格但公正的面试教练，正在给一场模拟面试打分并给出改进建议。

要求：
1. 严格按给定维度打分（1–5 分）。要有区分度，不要一律给 3 或 4 分。
2. 每个维度的评价必须引用候选人的原话作为证据，evidence_turn_ids 填对应回答回合的 id。
3. 说人话：问题是什么、当时说了什么、应该怎么说。
4. corrected_answer 必须用**候选人自己的经历**重写，不要给通用模板话术。
   候选人没提到的信息不要编造；缺信息就写成"补上具体数字"这样的占位提示。
5. 至少给 3 条改进项，按严重程度排序（high → medium → low）。
6. 全部用中文。
7. **术语用规范写法**：这是机器视觉方向的面试，说错术语比说错观点更扣分。
   例如「亚像素」不是「亚相素」、「仿射变换」不是「放射变换」、「九点标定」不是「九点标订」。
   常用术语（照着用，不要另造词）：
{GLOSSARY}

只输出一个 JSON 对象，不要输出任何其他内容，格式：
{
  "overall": 0-100 的总分,
  "summary": "3-5 句话的整体评价：先说最能提分的一条，再说最该补的一条",
  "dimensions": {
    "<维度 key>": {"score": 1-5, "comment": "一句话评价（含原话引用）", "evidence_turn_ids": [回答回合 id]}
  },
  "items": [
    {
      "severity": "high|medium|low",
      "dimension": "维度 key",
      "problem": "一句话说清问题",
      "evidence_turn_ids": [回答回合 id],
      "suggestion": "具体怎么改（可执行）",
      "corrected_answer": "用候选人自己的经历重写的示范回答",
      "drill": "立刻可做的练习",
      "topics": ["能力标签，用于后续针对性训练"]
    }
  ]
}
"""

# 术语清单用 replace 而不是 format 注入：prompt 里含 JSON 示例，
# 大括号会被 str.format 当成占位符直接报错。
SCORING_SYSTEM_PROMPT = SCORING_SYSTEM_PROMPT.replace("{GLOSSARY}", glossary_for_prompt())

# 结构化输入的包裹标记定义在协议层（providers/llm/base.py），
# 真实模型与 Mock 共用同一份约定，避免两边各写一份字符串而悄悄跑偏

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
MAX_ITEMS = 8


@dataclass(slots=True)
class Exchange:
    """一组问答：面试官问了什么，候选人答了什么。"""

    question_turn_id: int
    question: str
    question_kind: str = ""
    is_followup: bool = False
    answer_turn_id: int | None = None
    answer: str = ""
    answer_ms: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "question_turn_id": self.question_turn_id,
            "question": self.question,
            "question_kind": self.question_kind,
            "is_followup": self.is_followup,
            "answer_turn_id": self.answer_turn_id,
            "answer": self.answer,
            "answer_ms": self.answer_ms,
        }


@dataclass(slots=True)
class DimensionScore:
    key: str
    label: str
    score: int
    comment: str = ""
    evidence_turn_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ImprovementItemOut:
    severity: str
    dimension: str
    dimension_label: str
    problem: str
    suggestion: str
    corrected_answer: str
    drill: str
    evidence_turn_ids: list[int] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedReport:
    overall: int
    summary: str
    dimensions: list[DimensionScore]
    items: list[ImprovementItemOut]

    @property
    def weakest(self) -> DimensionScore | None:
        if not self.dimensions:
            return None
        return min(self.dimensions, key=lambda item: item.score)

    def dimensions_payload(self) -> dict[str, Any]:
        """落库形状（`score_report.dimension_scores`）。

        用 {key: {...}} 而不是数组：M9/M11/M12 加新维度时不会打乱既有读取逻辑。
        """
        return {
            dimension.key: {
                "label": dimension.label,
                "score": dimension.score,
                "comment": dimension.comment,
                "evidence_turn_ids": dimension.evidence_turn_ids,
            }
            for dimension in self.dimensions
        }


# --------------------------------------------------------------------------- #
# 输入构造
# --------------------------------------------------------------------------- #
def build_exchanges(turns: Sequence[SessionTurn]) -> list[Exchange]:
    """把回合序列折叠成问答对。

    规则：收尾语（kind=closing）不算题目；一条问题对应一条回答；
    出现"没有对应问题的回答"时单独成条，避免内容被静默丢掉。
    """
    exchanges: list[Exchange] = []
    pending: Exchange | None = None

    for turn in turns:
        if turn.speaker == "interviewer":
            if turn.kind == "closing":
                pending = None
                continue
            pending = Exchange(
                question_turn_id=turn.id or 0,
                question=turn.text,
                question_kind=turn.kind,
                is_followup=turn.is_followup,
            )
            exchanges.append(pending)
        elif turn.speaker == "candidate":
            if pending is None:
                pending = Exchange(question_turn_id=0, question="（没有对应问题的回答）", question_kind="orphan")
                exchanges.append(pending)
            pending.answer_turn_id = turn.id
            pending.answer = turn.text
            if turn.start_ms is not None and turn.end_ms is not None:
                pending.answer_ms = max(0, turn.end_ms - turn.start_ms)
            pending = None

    return exchanges


def build_scoring_messages(
    *,
    target_label: str,
    resume_text: str,
    duration_ms: int | None,
    max_questions: int,
    exchanges: Sequence[Exchange],
) -> list[LLMMessage]:
    payload = {
        "target": target_label,
        "resume_excerpt": (resume_text or "")[:2000],
        "duration_ms": duration_ms,
        "planned_question_count": max_questions,
        "exchanges": [exchange.to_payload() for exchange in exchanges],
    }

    user_content = (
        "请给下面这场面试打分。\n\n"
        f"{RECORD_MARKER_START}\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        f"{RECORD_MARKER_END}\n\n"
        f"{build_rubric_prompt()}"
    )
    return [
        LLMMessage(role="system", content=SCORING_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]


# --------------------------------------------------------------------------- #
# 输出规范化
# --------------------------------------------------------------------------- #
def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clean_ids(value: Any, valid_turn_ids: set[int]) -> list[int]:
    """只保留真实存在的回合 id，并去重、保持顺序。"""
    if not isinstance(value, list):
        return []
    seen: list[int] = []
    for item in value:
        try:
            turn_id = int(item)
        except (TypeError, ValueError):
            continue
        if turn_id in valid_turn_ids and turn_id not in seen:
            seen.append(turn_id)
    return seen


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dimension_lookup(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_dimensions = raw.get("dimensions")
    if isinstance(raw_dimensions, dict):
        return {str(key): item for key, item in raw_dimensions.items() if isinstance(item, dict)}
    if isinstance(raw_dimensions, list):
        lookup: dict[str, dict[str, Any]] = {}
        for item in raw_dimensions:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("dimension")
            if key:
                lookup[str(key)] = item
        return lookup
    return {}


def normalize_report(raw: Any, *, valid_turn_ids: set[int]) -> NormalizedReport:
    """把模型输出收敛成确定形状：分数 clamp、缺项补默认、证据过滤、建议排序。"""
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    lookup = _dimension_lookup(data)

    dimensions: list[DimensionScore] = []
    for definition in DIMENSIONS:
        entry = lookup.get(definition.key)
        if entry is None:
            dimensions.append(
                DimensionScore(
                    key=definition.key,
                    label=definition.label,
                    score=3,
                    comment="（模型未给出该项评分，按 3 分占位）",
                )
            )
            continue
        dimensions.append(
            DimensionScore(
                key=definition.key,
                label=definition.label,
                score=_clamp_int(entry.get("score"), 1, 5, 3),
                comment=_as_text(entry.get("comment")),
                evidence_turn_ids=_clean_ids(entry.get("evidence_turn_ids"), valid_turn_ids),
            )
        )

    scores = [dimension.score for dimension in dimensions]
    computed_overall = round(sum(scores) / len(scores) * 20) if scores else 0
    overall = _clamp_int(data.get("overall"), 0, 100, computed_overall)

    summary = _as_text(data.get("summary")) or "（模型未给出整体评价）"

    weakest = min(dimensions, key=lambda item: item.score) if dimensions else None
    items: list[ImprovementItemOut] = []
    raw_items = data.get("items")
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue

        severity = _as_text(item.get("severity")).lower()
        if severity not in SEVERITY_ORDER:
            severity = "medium"

        dimension_key = _as_text(item.get("dimension"))
        if not is_valid_dimension(dimension_key):
            # 与其丢掉这条建议，不如挂到最薄弱的维度上
            dimension_key = weakest.key if weakest is not None else DIMENSIONS[0].key

        topics = [text for text in (_as_text(topic) for topic in (item.get("topics") or [])) if text][:5]

        items.append(
            ImprovementItemOut(
                severity=severity,
                dimension=dimension_key,
                dimension_label=label_for(dimension_key),
                problem=_as_text(item.get("problem")),
                evidence_turn_ids=_clean_ids(item.get("evidence_turn_ids"), valid_turn_ids),
                suggestion=_as_text(item.get("suggestion")),
                corrected_answer=_as_text(item.get("corrected_answer")),
                drill=_as_text(item.get("drill")),
                topics=topics,
            )
        )

    items.sort(key=lambda improvement: SEVERITY_ORDER[improvement.severity])
    items = items[:MAX_ITEMS]

    if not items:
        # 空报告对用户毫无价值，兜底给一条可执行的提示
        fallback_dimension = weakest.key if weakest is not None else DIMENSIONS[0].key
        items.append(
            ImprovementItemOut(
                severity="medium",
                dimension=fallback_dimension,
                dimension_label=label_for(fallback_dimension),
                problem="模型没有给出具体改进项",
                suggestion="点击重新生成；若反复如此，请在 .env 里确认 LLM_PROVIDER=deepseek（reasoner 模型更适合批改）",
                corrected_answer="",
                drill="",
            )
        )

    return NormalizedReport(overall=overall, summary=summary, dimensions=dimensions, items=items)
