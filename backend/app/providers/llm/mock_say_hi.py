"""Mock 打招呼语：从 JD 与简历里各取一点，拼出一条像人写的消息。

不是随便糊一句：它会真的读输入——
从 JD 里挑一个技术关键词，再从简历里挑一个带数字的句子，
所以不同岗位/简历会得到不同招呼语（这样"AI 招呼语"这条链路才被真正验证过）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

#: 从 JD 里找"技术关键词"的候选词表（Mock 用，真实模型不需要）
TECH_HINTS = (
    "python",
    "java",
    "go",
    "golang",
    "c++",
    "redis",
    "mysql",
    "kafka",
    "消息队列",
    "高并发",
    "分布式",
    "微服务",
    "性能优化",
    "订单",
    "交易",
    "支付",
    "推荐",
    "风控",
    "容器",
    "k8s",
    "云原生",
)

_NUMBER_SENTENCE = re.compile(r"[^。；！？\n]*\d[^。；！？\n]*[。；！？]?")


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


def _pick_keyword(jd_text: str) -> str:
    lowered = (jd_text or "").lower()
    for hint in TECH_HINTS:
        if hint in lowered:
            return hint
    return ""


def _pick_achievement(resume_text: str) -> str:
    """从简历里挑一句带数字的成果（最能当"证据"的一句话）。"""
    match = _NUMBER_SENTENCE.search(resume_text or "")
    if match:
        sentence = match.group(0).strip("。；； ")
        if sentence:
            return sentence[:40]
    return ""


def build_mock_say_hi(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    title = str(record.get("title") or "")
    company = str(record.get("company") or "")
    jd_text = str(record.get("jd_excerpt") or "")
    resume_text = str(record.get("resume_excerpt") or "")

    keyword = _pick_keyword(jd_text)
    achievement = _pick_achievement(resume_text)

    parts: list[str] = []
    if keyword:
        parts.append(f"看到岗位在招{keyword}方向，我这两年正好在做这块")
    else:
        parts.append(f"看到{title or '这个岗位'}在招人，我的经历比较对口")

    if achievement:
        parts.append(f"其中一段经历是：{achievement}")
    else:
        parts.append("具体项目细节可以在沟通里展开说")

    if company:
        parts.append(f"方便的话想跟您聊聊{company}这个岗位")

    text = "，".join(parts) + "。"
    return text.replace("，，", "，").replace("。。", "。")
