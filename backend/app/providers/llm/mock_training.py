"""Mock 的出题与批改：让 M5 在没有 Key 的情况下也能走完整个训练闭环。

同样不是随便返回固定值：
- 出题会**带上真正薄弱的标签**（这样"针对性训练"在 Mock 下也成立）；
- 批改会真的读回答（数字、结构词、长度），答得不一样分就不一样。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|ms|秒|qps|万|亿|倍|人|次|天|元)?", re.IGNORECASE)
_STRUCTURE_WORDS = ("首先", "其次", "然后", "最后", "第一", "第二", "因为", "所以", "结论", "结果是")
_FILLERS = ("嗯", "那个", "就是说", "然后吧")

# 每个标签给两个模板：Mock 生成第二道同标签的题时能给出不同的题干，
# 否则题库里会堆出一模一样的题（真实模型也会给出相似题，所以入库前还要去重）
_QUESTION_TEMPLATES: dict[str, tuple[str, ...]] = {
    "量化结果": (
        "请举一个你最近的项目，用具体数字说明它带来了多少提升。",
        "你说过「效果不错」——如果面试官追问「具体多少」，你怎么答？",
    ),
    "结果导向": (
        "你做过的事情里，哪一件的效果最容易被人质疑？你用什么数据回应的？",
        "如果让你为自己的工作挑一个最重要的指标，你会挑哪个？为什么是它？",
    ),
    "结构化表达": (
        "用一分钟讲清楚你最近主导的一件事，要求结论先行。",
        "把同一个项目讲两遍：第一遍给技术负责人听，第二遍给不懂技术的 HR 听。",
    ),
    "STAR": (
        "讲一次你独立解决线上故障的经历，按背景、任务、行动、结果说。",
        "讲一次你没做成的事，同样是背景、任务、行动、结果，重点讲你怎么复盘的。",
    ),
    "技术取舍": (
        "说一个你否掉的技术方案，为什么否掉？代价是什么？",
        "你做过的最有争议的一个技术决策是什么？反对的人理由是什么？",
    ),
    "方案对比": (
        "同一个需求你对比过哪几种实现方案？最后怎么选的？",
        "如果现在让你重做那个系统，你会改哪一处设计？为什么？",
    ),
    "表达流畅度": (
        "把刚才那道题重讲一遍，要求不出现口头禅。",
        "用 30 秒把最复杂的一段经历讲完，不能有「然后」「那个」。",
    ),
    "切题": (
        "面试官问「为什么离职」，请用一句话正面回答，不要绕。",
        "面试官问「你最大的缺点是什么」，请正面回答并给出改进动作。",
    ),
    "听题": (
        "面试官的问题是「你最大的缺点是什么」，你会先回答哪一句？",
        "如果面试官问「这个项目你只是参与了还是主导了」，你怎么界定？",
    ),
    "时间控制": (
        "把「介绍你的核心项目」压到 90 秒讲完，先给结论。",
        "用 60 秒讲完同一个项目的最难技术点，不能超时。",
    ),
    "反问": (
        "你准备向面试官提哪三个问题？每个问题为什么对你重要？",
        "面试最后 5 分钟，只能问一个问题，你问哪个？",
    ),
    "岗位理解": (
        "你认为这个岗位半年内最需要解决的问题是什么？依据是什么？",
        "你觉得这个岗位最难的地方在哪？你准备怎么补？",
    ),
}

_GENERIC_TEMPLATES: tuple[str, ...] = (
    "围绕「{topic}」讲一个你自己的真实经历，并说明结果与反思。",
    "针对「{topic}」，如果面试官追问细节，你能说出哪些具体数据或取舍？",
)


def _extract_record(messages: Sequence[LLMMessage]) -> dict[str, Any]:
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


def build_mock_training_questions(messages: Sequence[LLMMessage]) -> str:
    """根据请求里的薄弱标签，生成一批针对性的练习题。"""
    record = _extract_record(messages)
    topics = [str(topic) for topic in (record.get("topics") or []) if str(topic).strip()]
    count = int(record.get("count") or 3)
    target = str(record.get("target") or "")
    avoid = {str(text) for text in (record.get("avoid") or [])}

    if not topics:
        topics = ["项目经历"]

    questions: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    for index in range(max(1, count)):
        topic = topics[index % len(topics)]
        used = usage.get(topic, 0)
        usage[topic] = used + 1
        options = _QUESTION_TEMPLATES.get(topic) or tuple(
            template.format(topic=topic) for template in _GENERIC_TEMPLATES
        )
        # 优先给没出过的题干；都出过了才回落（去重会拦住重复，所以不会污染题库）
        fresh = tuple(option for option in options if option not in avoid)
        pool = fresh or options
        text = pool[used % len(pool)]
        if target:
            text = f"（{target}）{text}"
        questions.append(
            {
                "text": text,
                "type": "training",
                "difficulty": 3,
                "tags": [topic],
                "reference_points": [
                    f"正面回答与「{topic}」相关的那个点",
                    "给出可核对的事实或数字",
                    "说明取舍或反思，而不是只讲过程",
                ],
            }
        )

    return json.dumps({"questions": questions}, ensure_ascii=False)


def build_mock_training_grade(messages: Sequence[LLMMessage]) -> str:
    """对一次训练作答打分（启发式，但真的读内容）。"""
    record = _extract_record(messages)
    answer = str(record.get("answer") or "")
    question = str(record.get("question") or "")

    numbers = _NUMBER.findall(answer)
    structure_hits = sum(answer.count(word) for word in _STRUCTURE_WORDS)
    filler_hits = sum(answer.count(word) for word in _FILLERS)
    length = len(answer)

    score = 2
    if length >= 60:
        score += 1
    if numbers:
        score += 1
    if structure_hits >= 1:
        score += 1
    if filler_hits >= 2:
        score -= 1
    score = max(1, min(5, score))

    missing: list[str] = []
    if not numbers:
        missing.append("补一个可核对的数字")
    if structure_hits == 0:
        missing.append("先给结论再展开")
    if filler_hits >= 2:
        missing.append("去掉口头禅")

    comment = "（Mock 模式启发式批改）" + (
        "；".join(missing) if missing else "回答已经比较完整，继续保持"
    )
    better = (
        f"（示范）针对「{question[:24]}」：先一句话给结论，再补一个数字证明它，最后说一句取舍。"
        f"你原话是「{answer[:24]}…」，把数字补进第二句即可。"
        if answer
        else ""
    )

    return json.dumps(
        {
            "score": score,
            "comment": comment,
            "better_answer": better,
            "signals": {
                "numbers": len(numbers),
                "structure_hits": structure_hits,
                "filler_hits": filler_hits,
                "length": length,
            },
        },
        ensure_ascii=False,
    )
