"""Mock 的问题打标：把问题单元分类成题型（真读文本，按关键词规则）。

不是为了"糊弄过去"：题型分布是画像的核心参数之一（决定模拟时的出题配比），
所以即便在 Mock 下，不同内容的问题也必须得到不同的分类，
否则"两段录音产出两个不同画像"这条验收就无从谈起。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

# 顺序有意义：先匹配具体的（自我介绍/算法），再匹配泛化的（项目/技术）
_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("self_intro", ("自我介绍", "介绍一下自己", "做个介绍", "介绍一下你")),
    ("reverse", ("有什么想问", "想问我们", "反问", "想了解", "你有什么问题")),
    ("salary", ("薪资", "期望待遇", "期望薪资", "package", "offer")),
    ("algorithm", ("算法", "复杂度", "手写", "写个函数", "leetcode", "编程题")),
    ("design", ("设计一个", "系统设计", "架构设计", "海量", "千万级", "亿级", "怎么设计")),
    ("scenario", ("如果", "假设", "突然", "线上故障", "挂了", "涨了十倍", "流量暴增")),
    ("behavior", ("冲突", "失败", "压力", "沟通", "团队合作", "最难", "挑战", "为什么离开", "离职")),
    ("project_deep", ("项目", "负责", "方案", "重构", "做了什么", "讲讲你", "谈一谈")),
    ("tech", ("原理", "实现", "性能", "并发", "缓存", "数据库", "为什么这样", "怎么保证", "细节")),
)

_TOPIC_LABELS = {
    "self_intro": "自我介绍",
    "project_deep": "项目深挖",
    "tech": "技术细节",
    "behavior": "行为面试",
    "algorithm": "算法题",
    "design": "系统设计",
    "scenario": "场景题",
    "salary": "薪资与期望",
    "reverse": "反问环节",
    "general": "通用问题",
}


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


def classify_question(question: str) -> str:
    """按关键词规则判断题型；匹配不到就 general。"""
    text = question or ""
    for question_type, keywords in _TYPE_RULES:
        if any(keyword in text for keyword in keywords):
            return question_type
    return "general"


def build_mock_pacing_labels(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    questions = [item for item in (record.get("questions") or []) if isinstance(item, dict)]

    labels: dict[str, dict[str, Any]] = {}
    for item in questions:
        idx = item.get("idx")
        question = str(item.get("question") or "")
        if idx is None:
            continue
        question_type = classify_question(question)
        labels[str(idx)] = {
            "type": question_type,
            "topic": _TOPIC_LABELS.get(question_type, "通用问题"),
            # 参考要点在 Mock 下给通用三条：真实模型会针对题目给具体要点
            "key_points": [
                "正面回答被问的那个点",
                "给出可核对的事实或数字",
                "说明取舍或反思",
            ],
        }

    return json.dumps({"labels": labels}, ensure_ascii=False)
