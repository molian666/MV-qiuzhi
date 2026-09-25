"""中英双语：维度标签、面试指令、以及**按语言切分的填充词表**。

英文面试不是"把 prompt 翻译一遍"：
- 评分报告是给人看的，中英混排很别扭，所以维度名要跟着语言走；
- 你说英文时的填充词是 um / uh / you know，跟中文的「嗯 / 那个」不是一回事；
- 英文技术面更常问 trade-off 与 impact，中文面更常问具体实现。

所以这里统一放"跟着语言变的那部分"，其余逻辑保持不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPORTED_LOCALES: tuple[str, ...] = ("zh", "en")


@dataclass(frozen=True, slots=True)
class LocaleProfile:
    key: str
    label: str
    #: 给模型的语言指令
    instruction: str
    #: 表达体检的提示（英文面的填充词与中文面不是一回事）
    speech_note: str
    #: 强填充词（明显卡壳）
    strong_fillers: tuple[str, ...]
    #: 弱填充词（口头习惯）
    weak_fillers: tuple[str, ...]


LOCALES: dict[str, LocaleProfile] = {
    "zh": LocaleProfile(
        key="zh",
        label="中文",
        instruction="全程用中文提问与追问，可以用中文的口语节奏。",
        speech_note="中文的「嗯 / 那个 / 就是说」是最常见的卡壳信号。",
        strong_fillers=(
            "嗯",
            "呃",
            "啊",
            "那个",
            "就是说",
            "怎么说呢",
            "对不对",
            "是吧",
            "你知道吗",
        ),
        weak_fillers=(
            "然后",
            "就是",
            "其实",
            "反正",
            "所以说",
            "我觉得吧",
            "然后呢",
            "接下来呢",
            "那个时候",
        ),
    ),
    "en": LocaleProfile(
        key="en",
        label="English",
        instruction=(
            "Conduct the entire interview in English (questions and follow-ups). "
            "Keep the language natural — do not translate Chinese idioms literally."
        ),
        speech_note="英文面里 um / uh / you know / I mean 是最常见的卡壳信号。",
        # 注意：这些词必须按**词边界**匹配，「um」不该命中「number」、「er」不该命中「over」
        strong_fillers=(
            "um",
            "uh",
            "er",
            "you know",
            "i mean",
            "kind of",
            "how do i say",
        ),
        weak_fillers=(
            "basically",
            "actually",
            "sort of",
            "anyway",
            "like",
            "right",
            "so yeah",
        ),
    ),
}

#: 维度 key → 英文标签（中文标签在 analysis/rubric.py 里）
DIMENSION_LABELS_EN: dict[str, str] = {
    "relevance": "Relevance",
    "structure": "Structure",
    "quantification": "Specificity & Metrics",
    "depth": "Technical Depth",
    "delivery": "Delivery",
    "time_control": "Time Management",
    "initiative": "Initiative",
    "expression": "Delivery Check",
}

#: 题型 key → 英文标签（中文标签在 analysis/interviewer_styles.py 里）
TOPIC_LABELS_EN: dict[str, str] = {
    "self_intro": "Self-introduction",
    "project_deep": "Project deep-dive",
    "tech": "Technical details",
    "behavior": "Behavioral",
    "algorithm": "Algorithms",
    "scenario": "Scenario",
    "salary": "Compensation",
    "reverse": "Your questions",
    "general": "General",
    "followup": "Follow-up",
}


def resolve_locale(key: str | None) -> LocaleProfile:
    return LOCALES.get(str(key or "").strip().lower()) or LOCALES["zh"]


def locale_prompt_block(locale: str | LocaleProfile | None) -> str:
    profile = locale if isinstance(locale, LocaleProfile) else resolve_locale(locale)
    return f"【语言：{profile.label}】{profile.instruction}"


def dimension_label(key: str, locale: str | LocaleProfile | None, *, fallback: str = "") -> str:
    """维度标签（英文面用英文名，避免报告里中英混排）。"""
    profile = locale if isinstance(locale, LocaleProfile) else resolve_locale(locale)
    if profile.key == "en":
        return DIMENSION_LABELS_EN.get(key, fallback or key)
    return fallback or key


def topic_label(key: str, locale: str | LocaleProfile | None, *, fallback: str = "") -> str:
    profile = locale if isinstance(locale, LocaleProfile) else resolve_locale(locale)
    if profile.key == "en":
        return TOPIC_LABELS_EN.get(key, fallback or key)
    return fallback or key


def filler_words(locale: str | LocaleProfile | None, *, include_weak: bool = True) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """返回 (强填充词, 弱填充词)。"""
    profile = locale if isinstance(locale, LocaleProfile) else resolve_locale(locale)
    return profile.strong_fillers, profile.weak_fillers if include_weak else ()


def locale_options() -> list[dict[str, Any]]:
    return [
        {"key": profile.key, "label": profile.label, "speech_note": profile.speech_note}
        for profile in LOCALES.values()
    ]
