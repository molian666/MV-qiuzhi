"""Mock 故事卡：从简历里真正切出几段经历来生成 STAR 结构。

它不是随便填一句话：按行扫描挑出像"经历描述"的段落（优先含数字的），
再套进 STAR 四段，技能标签与主题都从原文里抽。
所以换一份简历会得到不同的故事卡——这条链路才算被真正验证过。

**结果字段的处理说明**：`story_card.REQUIRED_FIELDS` 要求 result 非空
（缺结果的故事卡在面试里引用反而危险）。Mock 无法从简历里推断影响，
所以它把"含数字的那句"当结果；没有数字时就写一句明确的占位提醒，
而不是编一个漂亮的结果。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from ...analysis.ats import METRIC as _METRIC
from ...analysis.skill_dict import find_skills
from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

RESULT_PLACEHOLDER = "（简历未写明结果——请补充这一段带来的实际影响）"

_PHONE = re.compile(r"1[3-9]\d{9}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_TAG_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quality", ("故障", "事故", "稳定", "可用性", "线上问题", "崩溃", "bug", "告警")),
    ("efficiency", ("优化", "提升", "降低", "加速", "性能", "自动化", "重构", "瓶颈")),
    ("leadership", ("主导", "牵头", "带", "推动", "负责团队", "协调", "管理")),
    ("challenge", ("难点", "难题", "攻坚", "复杂", "挑战", "高并发", "分布式")),
    ("growth", ("学习", "自学", "转型", "从零", "新技术")),
    ("conflict", ("冲突", "分歧", "说服", "跨部门", "沟通")),
    ("failure", ("失败", "教训", "回滚", "延期", "事故复盘")),
)


def _extract_record(messages: Sequence[LLMMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        text = message.content
        start = text.find(RECORD_MARKER_START)
        end = text.find(RECORD_MARKER_END)
        if start == -1 or end <= start:
            continue
        try:
            data = json.loads(text[start + len(RECORD_MARKER_START) : end].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _candidate_lines(resume_text: str) -> list[str]:
    """挑出像"一段经历"的行：够长、不是联系方式。

    联系方式是**原地剥掉**而不是整行丢弃：真实简历里它通常单独一行，
    但「…把 QPS 从 800 提到 4000 ｜ 138xxxxxxxx」这种写法也不少，
    整行丢掉会连经历一起丢掉（这个坑实测踩到过）。
    """
    lines: list[str] = []
    for raw_line in (resume_text or "").splitlines():
        cleaned = _EMAIL.sub(" ", raw_line)
        cleaned = _PHONE.sub(" ", cleaned)
        line = cleaned.strip().lstrip("-•·*0123456789.、) ｜|")
        if len(line) < 12 or len(line) > 200:
            continue
        lines.append(line)
    return lines


def _pick_lines(lines: Sequence[str], count: int) -> list[str]:
    """优先带数字的（信息量最大），其次按原顺序补足。"""
    with_metric = [line for line in lines if _METRIC.search(line)]
    picked: list[str] = []
    for line in with_metric + list(lines):
        if line not in picked:
            picked.append(line)
        if len(picked) >= count:
            break
    return picked


def _guess_tags(text: str) -> list[str]:
    tags = [tag for tag, cues in _TAG_CUES if any(cue in text for cue in cues)]
    return tags[:2] or ["achievement"]


def _to_title(text: str) -> str:
    head = re.split(r"[，。；,;:：]", text)[0].strip()
    return (head or text)[:20]


def build_mock_stories(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    resume_text = str(record.get("resume_excerpt") or "")
    existing = {str(item).strip().lower() for item in (record.get("already_have") or [])}
    count = int(record.get("how_many") or 4)

    lines = _pick_lines(_candidate_lines(resume_text), max(count + 2, 4))

    stories: list[dict[str, Any]] = []
    for line in lines:
        title = _to_title(line)
        if title.lower() in existing:
            continue

        metric_match = _METRIC.search(line)
        skills = list(find_skills(line).keys())[:4]

        stories.append(
            {
                "title": title,
                "situation": f"简历原文：{line}",
                "task": "",
                "action": line,
                "result": metric_match.group(0).strip() if metric_match else RESULT_PLACEHOLDER,
                "metrics": metric_match.group(0).strip() if metric_match else "",
                "tags": _guess_tags(line),
                "topics": skills,
            }
        )
        if len(stories) >= count:
            break

    return json.dumps({"stories": stories}, ensure_ascii=False)
