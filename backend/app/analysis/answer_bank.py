"""话术库：自我介绍（3 个时长）、敏感题、反问清单。

**所有话术都从你的简历与故事卡里长出来**，不是通用模板——
"我学习能力强、抗压能力好"这种话 HR 一天要听 20 遍，说了等于没说。

敏感题部分写入了**策略**（怎么说）而不是事实（说什么）：
策略可以给，因为它是通行做法（比如"离职原因不要抱怨前雇主"）；
事实必须你自己有，所以 prompt 明确禁止模型编造在职经历或数字。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ..models.story import STORY_TAGS
from ..providers.llm.base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage
from .story_card import StoryRef

#: 中文口语大约每分钟 200 字（正常语速、不赶不拖）
CHARS_PER_SECOND = 3.3

SELF_INTRO_LENGTHS: tuple[int, ...] = (30, 60, 180)

#: 敏感题：key → (标题, 面试官原话, 策略)
SENSITIVE_QUESTIONS: dict[str, tuple[str, str, str]] = {
    "leave": (
        "离职原因",
        "你为什么离开上一家公司？",
        "把重点放在「我想去哪里」而不是「上一家有多差」：不提抱怨前雇主/同事、不提"
        "「钱少活多」。说清两点——过去这段经历让你确认了自己想深耕的方向，而这个方向正好是这个岗位在做的事。"
        "如果确实是被裁/项目砍掉，直说即可（这很常见，不丢人），但一定接上你之后的动作。",
    ),
    "gap": (
        "空窗期",
        "这段空窗期你在做什么？",
        "如实说这段时间在做什么，越具体越好：在学什么、做了什么项目、处理了什么家事。"
        "空窗期本身不是问题，「说不清在干什么」才是问题。**不要编一段在职经历**——"
        "背调很容易查出来，代价远大于空窗本身。",
    ),
    "plan": (
        "职业规划",
        "未来三年你的规划是什么？",
        "给一条与这个岗位挂钩的路径（先把当前方向做扎实 → 能独立负责一块业务 → 影响团队的技术选型），"
        "不要说「想创业」「想转管理岗」「想换方向」——招聘方关心的是你留得住、成长方向与岗位一致。",
    ),
    "salary": (
        "期望薪资",
        "你的期望薪资是多少？",
        "先反问对方的薪资区间，再给区间而不是一个死数。区间的依据要说得出来：当前薪资 + 市场行情 + 岗位范围。"
        "如果被追问底线，给一个你能接受的数字，并说明「包含绩效/年包口径」这类前提。",
    ),
    "weakness": (
        "最大的不足",
        "你觉得自己最大的不足是什么？",
        "说一个真实但不致命、并且你已经在改的不足，配一个具体动作（比如「以前不爱写文档，"
        "现在每个项目都会留设计说明」）。不要用「我太追求完美」这种假缺点，面试官听过太多次。",
    ),
    "job_change": (
        "频繁跳槽",
        "你为什么换工作这么频繁？",
        "把几次变动串成一条主动选择的线（每次是为了什么能力/方向），而不是被动离开。"
        "如果中间有一次确实是判断失误，承认它并说清你从中学到了什么。",
    ),
}

SENSITIVE_ORDER: tuple[str, ...] = tuple(SENSITIVE_QUESTIONS.keys())

SELF_INTRO_PROMPT = """你在帮求职者准备**口头**自我介绍。

硬规则：
1. **只使用提供的简历与故事卡里已有的事实**，不得新增公司、项目、技术栈或数字。
2. 时间只有 %d 秒，所以字数必须控制在 %d 字左右（中文口语约 3.3 字/秒）。
3. 结构：我是谁（经验年限 + 方向）→ 最相关的一段经历（带一个可核对的结果）→ 为什么对这个岗位感兴趣。
4. 用**口语**写，句子要能一口气说顺；不要书面语长句、不要列点、不要表情符号。
5. 不要出现"我是一个很有责任心的人""抗压能力强"这类自夸式空话——用经历证明，不用形容词。
6. 全部中文。只输出这一段话，不要任何解释或标题。
"""

SENSITIVE_PROMPT = """你在帮求职者准备一个**敏感问题的口头回答**。

面试官原话：「%s」
回答策略：%s

硬规则：
1. **只使用提供的简历与故事卡里已有的事实**。不得编造在职经历、离职原因或数字。
2. 允许留出需要本人补充的空白，用方括号标出来，例如「[补：你当时的实际考虑]」——
   留空白比编一个听起来漂亮的理由安全得多。
3. 字数控制在 %d 字以内（口头回答，别太长）。
4. 口语化，语气平静、不辩解、不抱怨。不要列点。
5. 全部中文。只输出这段话，不要解释。
"""

QUESTIONS_PROMPT = """你在帮求职者准备**面试最后的反问**。

要求：
1. 给出 %d 个问题，每个问题都要能**从面试官的回答里听出信息**——
   不要问"公司文化怎么样"这类只能得到套话的问题。
2. 至少包含：2 个关于岗位/团队实际状况的、1 个关于评价标准或成长路径的、1 个关于业务/技术挑战的。
3. 结合下面的 JD 与候选人背景，让问题显得"你真的看过这个岗位"。
4. 每个问题一行，不加序号、不加解释、不要 bullet 符号，全部中文。
"""


def char_budget(seconds: int) -> int:
    return max(40, int(seconds * CHARS_PER_SECOND))


def spoken_seconds(text: str) -> float:
    """估算这段话说出来要多久（用于自我介绍计时）。"""
    cleaned = re.sub(r"\s+", "", text or "")
    if not cleaned:
        return 0.0
    return round(len(cleaned) / CHARS_PER_SECOND, 1)


def _payload(*, resume_text: str, stories: Sequence[StoryRef], jd_text: str, extra: dict[str, Any]) -> str:
    story_lines: list[str] = []
    for card in stories:
        labels = "、".join(STORY_TAGS.get(tag, tag) for tag in card.tags)
        story_lines.append(
            f"- {card.title}（{labels}）：{card.action} → {card.result}"
            + (f"（数字：{card.metrics}）" if card.metrics else "")
        )

    payload = {
        "resume_excerpt": (resume_text or "")[:2500],
        "story_cards": story_lines[:6],
        "jd_excerpt": (jd_text or "")[:1500],
        **extra,
    }
    import json

    return f"{RECORD_MARKER_START}\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n{RECORD_MARKER_END}"


def build_self_intro_messages(
    *,
    resume_text: str,
    stories: Sequence[StoryRef] = (),
    jd_text: str = "",
    seconds: int = 60,
) -> list[LLMMessage]:
    budget = char_budget(seconds)
    system = SELF_INTRO_PROMPT % (seconds, budget)
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(
            role="user",
            content=_payload(
                resume_text=resume_text,
                stories=stories,
                jd_text=jd_text,
                extra={"target_seconds": seconds, "target_chars": budget},
            ),
        ),
    ]


def build_sensitive_messages(
    *,
    kind: str,
    resume_text: str,
    stories: Sequence[StoryRef] = (),
    jd_text: str = "",
) -> list[LLMMessage]:
    label, question, strategy = SENSITIVE_QUESTIONS.get(
        kind, ("敏感问题", "（未指定问题）", "如实作答，不编造经历。")
    )
    system = SENSITIVE_PROMPT % (question, strategy, char_budget(60))
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(
            role="user",
            content=_payload(
                resume_text=resume_text,
                stories=stories,
                jd_text=jd_text,
                extra={"kind": kind, "label": label, "question": question},
            ),
        ),
    ]


def build_questions_messages(
    *,
    jd_text: str,
    company: str = "",
    resume_text: str = "",
    count: int = 5,
) -> list[LLMMessage]:
    system = QUESTIONS_PROMPT % count
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(
            role="user",
            content=_payload(
                resume_text=resume_text,
                stories=(),
                jd_text=jd_text,
                extra={"company": company, "count": count},
            ),
        ),
    ]


def normalize_self_intro(text: str, *, seconds: int) -> str:
    """压成一段并收敛到目标字数（允许 ±20%——太短显得没准备，太长会被打断）。"""
    single = " ".join(part.strip() for part in (text or "").splitlines() if part.strip())
    single = re.sub(r"```[a-zA-Z]*", "", single).replace("```", "")
    single = re.sub(r"^\s*[-*•\d]+[.、)]\s*", "", single).strip()
    single = re.sub(r"\s{2,}", " ", single).strip()

    budget = char_budget(seconds)
    ceiling = int(budget * 1.2)
    if len(single) > ceiling:
        head = single[:ceiling]
        for mark in ("。", "！", "!", "；", ";"):
            cut = head.rfind(mark)
            if cut >= budget * 0.6:
                head = head[: cut + 1]
                break
        single = head.strip()
    return single


def normalize_lines(text: str, *, limit: int = 8) -> list[str]:
    """把模型输出的清单解析成一行一条。"""
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*[-*•·]+\s*", "", line)
        line = re.sub(r"^\s*\d+[.、)]\s*", "", line)
        line = line.strip()
        if len(line) < 4:
            continue
        if line not in lines:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines
