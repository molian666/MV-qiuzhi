"""Mock 话术：真的读简历与故事卡，拼出可用的草稿。

- **自我介绍**：按目标字数取「经验年限句 + 一条带数字的经历 + 一句岗位关联」；
- **敏感题**：策略句 + 一条来自简历的证据，并把必须本人补充的地方留成方括号空白
  （**不编造**——留空白才是对的做法，Mock 也一样守这条规则）；
- **反问清单**：从 JD 里抽关键词生成问题，所以不同岗位会得到不同的问题。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from ...analysis.ats import METRIC as STRONG_METRIC
from ...analysis.skill_dict import find_skills
from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

_PHONE = re.compile(r"1[3-9]\d{9}")

#: 敏感题的策略开场（与 answer_bank.SENSITIVE_QUESTIONS 的策略配套）
STRATEGY_LINES: dict[str, str] = {
    "leave": "离开上一家，主要是想在一个方向上做深，而不是换个地方接着做同样的事。",
    "gap": "那段时间我安排了比较明确的计划，不是空着。",
    "plan": "比较希望先在当前这个方向上做扎实，之后能独立负责一块业务。",
    "salary": "我想先了解一下这个岗位的薪资区间，再给出更准确的期望。",
    "weakness": "以前我在文档上有明显的短板，后来强迫自己每个项目都留设计说明。",
    "job_change": "回头看每次变动，都是在补一块我缺的能力。",
}

FOLLOW_UP_HINTS: dict[str, str] = {
    "leave": "（补：你当时的实际考虑，一句就够，别展开抱怨）",
    "gap": "（补：具体在学什么 / 做了什么项目 / 处理了什么家事）",
    "plan": "（补：与这个岗位挂钩的那一步）",
    "salary": "（补：你的区间与依据——当前薪资、市场行情、岗位范围）",
    "weakness": "（补：一个具体动作和它带来的变化）",
    "job_change": "（补：每次变动是为了补哪块能力）",
}


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


def _achievement_sentences(text: str) -> list[str]:
    """把简历里像"成果"的句子按可信度排序：带量化 > 带数字 > 其他描述。

    排序重要：自我介绍先说哪一条，面试官通常就先追哪一条。
    """
    sentences: list[str] = []
    for line in (text or "").splitlines():
        for piece in re.split(r"[。；！？\n]", line):
            cleaned = piece.strip().lstrip("-•·*0123456789.、) ｜|")
            if "@" in cleaned or _PHONE.search(cleaned):
                continue
            if 10 <= len(cleaned) <= 120 and cleaned not in sentences:
                sentences.append(cleaned)

    strong = [item for item in sentences if STRONG_METRIC.search(item)]
    weak = [item for item in sentences if item not in strong and any(char.isdigit() for char in item)]
    rest = [item for item in sentences if item not in strong and item not in weak]
    return strong + weak + rest


def _first_sentence_with_digit(text: str) -> str:
    sentences = _achievement_sentences(text)
    return sentences[0] if sentences else ""


def _story_line(story_lines: Sequence[str]) -> str:
    """从故事卡摘要里取出"结果"部分（带数字的那些最值得放在自我介绍里）。"""
    for line in story_lines:
        if "数字：" in line and "→" in line:
            return line.split("→", 1)[1].split("（数字：")[0].strip().rstrip("。")
    return ""


def _experience_phrase(resume_text: str) -> str:
    import re

    match = re.search(r"(\d+)\s*年", resume_text or "")
    skills = list(find_skills(resume_text or "").keys())
    direction = skills[0] if skills else ""
    if match:
        years = match.group(1)
        return f"{years} 年{direction}方向的开发经验" if direction else f"{years} 年开发经验"
    return f"{direction}方向的开发经验" if direction else "几段开发经验"


def build_mock_self_intro(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    resume_text = str(record.get("resume_excerpt") or "")
    story_lines = [str(item) for item in (record.get("story_cards") or [])]
    jd_text = str(record.get("jd_excerpt") or "")
    budget = int(record.get("target_chars") or 200)

    parts: list[str] = [f"我有{_experience_phrase(resume_text)}。"]

    # 讲故事优先用故事卡里带数字的结果（那是面试官会追的点）
    candidates: list[str] = []
    story_hint = _story_line(story_lines)
    if story_hint:
        candidates.append(story_hint)
    for sentence in _achievement_sentences(resume_text):
        if sentence not in candidates:
            candidates.append(sentence)

    for index, sentence in enumerate(candidates[:2]):
        lead = "其中比较有代表性的是：" if index == 0 else "另外我还做过："
        parts.append(f"{lead}{sentence}。")

    jd_skills = list(find_skills(jd_text).keys())[:2]
    if jd_skills:
        parts.append(f"看到这个岗位在做 {'、'.join(jd_skills)} 这块，正好是我一直想继续做的方向。")
    else:
        parts.append("看到这个岗位的方向，正好和我想继续做的一致。")

    # 仍明显短于目标时长时，再补一条真实经历——而不是灌水凑字数
    if len("".join(parts)) < budget * 0.75:
        for sentence in candidates[2:5]:
            parts.insert(-1, f"我也参与过：{sentence}。")
            if len("".join(parts)) >= budget * 0.75:
                break

    text = "".join(parts)
    if len(text) > budget:
        keep: list[str] = []
        for part in parts:
            if len("".join(keep)) + len(part) <= budget:
                keep.append(part)
        text = "".join(keep) or parts[0]
    return text


def build_mock_sensitive(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    kind = str(record.get("kind") or "")
    resume_text = str(record.get("resume_excerpt") or "")

    opening = STRATEGY_LINES.get(kind, "我按实际情况说。")
    evidence = _first_sentence_with_digit(resume_text)
    hint = FOLLOW_UP_HINTS.get(kind, "（补：你的实际情况）")

    parts = [opening]
    if evidence:
        parts.append(f"比如上一段经历里，{evidence}。")
    parts.append(hint)
    return "".join(parts)


def build_mock_questions(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    jd_text = str(record.get("jd_excerpt") or "")
    company = str(record.get("company") or "").strip()
    count = int(record.get("count") or 5)

    skills = list(find_skills(jd_text).keys())
    top = skills[0] if skills else "核心技术"
    second = skills[1] if len(skills) > 1 else "关键模块"

    candidates = [
        "这个岗位目前最需要解决的核心问题是什么？",
        f"团队在 {top} 这块现在的现状是怎样的，有哪些历史包袱？",
        "这个岗位前 3 个月的考核标准是什么？",
        f"{second} 相关的决策一般由谁拍板，是平台统一还是团队自定？",
        "团队现在的分工是怎样的，这个岗位是补空缺还是新增？",
        f"{company or '团队'} 在这个业务方向上接下来一年的主要挑战是什么？",
    ]
    return "\n".join(candidates[:count])
