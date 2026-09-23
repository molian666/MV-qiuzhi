"""Mock 评分：没有 Key 时也能产出一份**与输入相关**的报告。

它不是随便填几个数字：真的读输入（回答长度、数字密度、结构词、口头禅、回答时长），
所以不同回答会得到不同分数与不同建议——这样才能在没有 Key 的情况下验证
"报告页 + 落库 + 前端渲染"这整条链路，也才对得起"Mock 必须能跑通全流程"这条要求。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ms|秒|分钟|qps|万|亿|倍|人|次|天|元|w)?", re.IGNORECASE)
_STRUCTURE_WORDS = ("首先", "其次", "然后", "接下来", "最后", "第一", "第二", "因为", "所以", "结论", "结果是")
_FILLERS = ("嗯", "那个", "就是说", "然后吧")

# 每个维度的建议模板；corrected 里的 {quote} 会被替换成候选人自己的原话片段
_TEMPLATES: dict[str, dict[str, Any]] = {
    "quantification": {
        "problem": "回答里几乎没有可验证的数字，听不出你到底做成了什么",
        "suggestion": "每个项目结论都补一个可核对的指标（QPS / P99 延迟 / 成本 / 转化率），并给出改动前后的数值",
        "corrected": "把「{quote}」这句改成：「I 把 {subject} 从 A 提到了 B，为此付出的代价是 C」——数字可以事后查，但不能没有。",
        "drill": "挑最近一个项目，用三句话讲完：背景 1 句、做法 1 句（含量化结果）1 句。连续说三遍到不用想。",
        "topics": ["量化结果", "结果导向"],
    },
    "structure": {
        "problem": "回答没有骨架，听的人要自己拼信息",
        "suggestion": "先给结论再展开：一句话说结论，然后分 2–3 点讲做法，最后回到结果与反思",
        "corrected": "「{quote}」可以改成：「结论是 X。原因有两点：第一……第二……。最后结果是 Y。」",
        "drill": "拿这道题写 60 字提纲（结论 + 两点 + 结果），照着提纲口头说一遍并录音复听。",
        "topics": ["结构化表达", "STAR"],
    },
    "depth": {
        "problem": "停在“做了什么”，没讲为什么这么选、边界在哪",
        "suggestion": "补上取舍：对比过的替代方案、为什么否掉、这套方案的失效条件",
        "corrected": "「{quote}」后面可以补：「当时也考虑过 X，但它在 Y 场景下会 Z，所以选了现在这套；它的代价是……」",
        "drill": "给自己最近的技术选型找两个替代方案，各写一句“为什么不用”。",
        "topics": ["技术取舍", "方案对比"],
    },
    "delivery": {
        "problem": "表达里有明显填充词或卡顿，影响听感",
        "suggestion": "把「嗯 / 那个 / 就是说」换成停顿；语速放慢一点，重点句前先停半秒",
        "corrected": "重录一遍「{quote}」，目标是同样的内容里不出现填充词，并且每句话结尾咬清。",
        "drill": "每天用 2 分钟复述一段当天的工作，录音后数填充词数量，目标降到 3 个以内。",
        "topics": ["表达流畅度"],
    },
    "relevance": {
        "problem": "回答没有正面命中问题，绕开了提问的落点",
        "suggestion": "先用一句话直接回答被问的那个点，再补充背景",
        "corrected": "被问到这个问题时，第一句就要落在提问上：「{quote}」之前先加一句直接答案。",
        "drill": "把这场面试里每个问题写成一行，后面各写一句“直接答案”，练到能脱口而出。",
        "topics": ["切题", "听题"],
    },
    "time_control": {
        "problem": "单题回答时长偏离常见区间（技术/项目题约 1–3 分钟）",
        "suggestion": "给自己定 90 秒的软上限：讲完结论与两点支撑就收，剩下的留给追问",
        "corrected": "「{quote}」这段可以压到 90 秒：砍掉铺垫，只留结论、一个关键做法、一个数字结果。",
        "drill": "用手机计时练同一道题，先说 60 秒版本，再说 120 秒版本。",
        "topics": ["时间控制"],
    },
    "initiative": {
        "problem": "没有表现出对岗位的主动了解（反问环节偏弱）",
        "suggestion": "准备 3 个只针对这家公司/岗位的问题（业务现状、团队分工、这个岗位半年内的重点）",
        "corrected": "反问环节别问“加不加班”，改成：「你们这个岗位半年内最想解决的一个问题是什么？」",
        "drill": "针对目标岗位写 3 个反问，并各写一句“为什么这个问题对我重要”。",
        "topics": ["反问", "岗位理解"],
    },
}


def extract_record(messages: Sequence[LLMMessage]) -> dict[str, Any]:
    """从消息里抠出结构化记录（用协议层的包裹标记，不猜自然语言）。"""
    for message in reversed(messages):
        text = message.content
        start = text.find(RECORD_MARKER_START)
        end = text.find(RECORD_MARKER_END)
        if start == -1 or end <= start:
            continue
        blob = text[start + len(RECORD_MARKER_START) : end].strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _heuristic_report(record: dict[str, Any]) -> dict[str, Any]:
    exchanges = [item for item in (record.get("exchanges") or []) if isinstance(item, dict)]
    answers = [str(item.get("answer") or "") for item in exchanges]
    joined = " ".join(answers)

    numbers = _NUMBER.findall(joined)
    structure_hits = sum(joined.count(word) for word in _STRUCTURE_WORDS)
    filler_hits = sum(joined.count(word) for word in _FILLERS)

    answer_count = len(answers)
    average_length = sum(len(text) for text in answers) / max(1, answer_count)
    spans = [int(item.get("answer_ms") or 0) for item in exchanges if item.get("answer_ms")]
    average_ms = sum(spans) / max(1, len(spans)) if spans else 0

    relevance = 4 if answer_count and average_length >= 40 else (3 if answer_count else 2)
    structure = 4 if structure_hits >= 3 else (3 if structure_hits >= 1 else 2)
    quantification = 5 if len(numbers) >= 3 else (4 if numbers else 2)
    depth = 4 if (len(numbers) >= 2 and average_length >= 120) else (3 if average_length >= 60 else 2)
    delivery = 4 if (filler_hits <= 1 and average_length >= 40) else (3 if filler_hits <= 3 else 2)

    if average_ms == 0:
        time_control = 3
    elif average_ms < 20_000:
        time_control = 2
    elif average_ms <= 180_000:
        time_control = 4
    else:
        time_control = 3

    # 文字作答的"时长"其实是打字时间，用它判断时间控制会误伤：给个中性分并说明原因
    text_mode = 0 < average_ms < 10_000
    if text_mode:
        time_control = 3

    initiative = 3  # 模拟面试里用户往往没走反问环节，保守给中位分

    scores = {
        "relevance": relevance,
        "structure": structure,
        "quantification": quantification,
        "depth": depth,
        "delivery": delivery,
        "time_control": time_control,
        "initiative": initiative,
    }

    labels = {
        "relevance": "切题度",
        "structure": "结构",
        "quantification": "信息密度与量化",
        "depth": "技术深度与正确性",
        "delivery": "表达与流畅",
        "time_control": "时间控制",
        "initiative": "主动性",
    }

    dimensions = {
        key: {
            "score": value,
            "comment": (
                f"（Mock 模式启发式评分：{labels[key]} {value}/5，接入真实模型后会给出引用原话的评价）"
                if not (key == "time_control" and text_mode)
                else "（Mock 模式：本轮是文字作答，「时长」只是打字时间，无法真正评估时间控制，按中性分处理）"
            ),
            "evidence_turn_ids": _pick_evidence(key, exchanges),
        }
        for key, value in scores.items()
    }

    weakest_key = min(scores, key=lambda key: scores[key])
    overall = round(sum(scores.values()) / len(scores) * 20)
    summary = (
        f"（Mock 模式）本场共 {answer_count} 个回答，总分 {overall}。"
        f"最弱的一环是「{labels[weakest_key]}」（{scores[weakest_key]}/5）：{_TEMPLATES[weakest_key]['suggestion']}。"
        f"另外，回答里出现了 {len(numbers)} 处数字、{structure_hits} 处结构词、{filler_hits} 处填充词——"
        "接真实模型（LLM_PROVIDER=deepseek）后这里会换成针对原话的具体评价。"
    )

    items = _items_for(scores, exchanges, labels, skip={"time_control"} if text_mode else set())
    return {"overall": overall, "summary": summary, "dimensions": dimensions, "items": items}


def _pick_evidence(dimension_key: str, exchanges: list[dict[str, Any]]) -> list[int]:
    """挑出**最能说明这个维度问题**的那条回答，而不是无脑取第一条。

    每个维度给一个"越小越该被引用"的权重：
    数字越少 → 越能说明量化问题；结构词越少 → 越能说明结构问题；越偏离 90 秒 → 越能说明时长问题。
    """
    scored: list[tuple[float, int]] = []
    for item in exchanges:
        answer_turn_id = item.get("answer_turn_id")
        if not answer_turn_id:
            continue
        answer = str(item.get("answer") or "")
        answer_ms = int(item.get("answer_ms") or 0)

        if dimension_key == "quantification":
            weight: float = float(len(_NUMBER.findall(answer)))
        elif dimension_key == "structure":
            weight = float(sum(answer.count(word) for word in _STRUCTURE_WORDS))
        elif dimension_key == "delivery":
            weight = float(sum(answer.count(word) for word in _FILLERS))
        elif dimension_key == "time_control":
            weight = -abs(answer_ms - 90_000)
        else:
            weight = float(len(answer))

        scored.append((weight, int(answer_turn_id)))

    if not scored:
        return []
    scored.sort()
    return [scored[0][1]]


def _items_for(
    scores: dict[str, int],
    exchanges: list[dict[str, Any]],
    labels: dict[str, str],
    skip: set[str] | None = None,
) -> list[dict[str, Any]]:
    first_answer = next((str(item.get("answer") or "") for item in exchanges if item.get("answer")), "")
    quote = (first_answer[:24] + "…") if len(first_answer) > 24 else first_answer

    # skip 用来剔除"这一轮根本无法评估"的维度：没法评估还给建议就是误导
    skipped = skip or set()
    ordered = [
        (key, score)
        for key, score in sorted(scores.items(), key=lambda pair: pair[1])
        if key not in skipped
    ]

    items: list[dict[str, Any]] = []
    for key, score in ordered[:3]:
        template = _TEMPLATES.get(key)
        if template is None:
            continue
        severity = "high" if score <= 2 else ("medium" if score == 3 else "low")
        items.append(
            {
                "severity": severity,
                "dimension": key,
                "problem": f"{template['problem']}（{labels[key]} {score}/5）",
                "evidence_turn_ids": _pick_evidence(key, exchanges),
                "suggestion": template["suggestion"],
                "corrected_answer": template["corrected"].format(quote=quote, subject="这个项目"),
                "drill": template["drill"],
                "topics": template["topics"],
            }
        )
    return items


def build_mock_scoring_json(messages: Sequence[LLMMessage]) -> str:
    record = extract_record(messages)
    if not record:
        # 输入里没有记录（例如被别处调用）：给一份合法结构，避免链路炸掉
        record = {"exchanges": []}
    return json.dumps(_heuristic_report(record), ensure_ascii=False)
