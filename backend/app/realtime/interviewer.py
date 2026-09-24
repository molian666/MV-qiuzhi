"""面试官编排（M0 文字版；M1 接入实时语音后，这段逻辑要被直接复用）。

职责边界很重要：这里只回答"问什么、怎么追问"，**不碰音频、不碰数据库**。
落库与事件下发交给 api/services 层——这样 M1 的 WebSocket 链路可以原样复用本模块，
而 M11 的压力模式/追问树也只是换 prompt 与策略参数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models.core import JobTarget, Resume
from ..models.session import SessionTurn
from ..providers.llm.base import LLMClient, LLMMessage, LLMResult, extract_json

BASE_SYSTEM_PROMPT = """你是一位经验丰富、风格专业的技术面试官，正在对候选人进行模拟面试。

面试规则：
1. 每次只问一个问题，问题要具体、可回答；不要在一句话里塞多个问题。
2. 先听后问：必须结合候选人上一轮的回答追问，重点抓其中含糊、缺量化、缺取舍的地方。
3. 题型顺序：自我介绍 → 项目深挖 → 技术细节 → 行为与软技能 → 反问环节。
4. 像真人面试官一样说话：简洁、专业；不评价、不夸奖、不替候选人总结。
5. 候选人跑题时，用一句话拉回来，然后重新提问。
6. 不要暴露你是 AI，不要提到"规则"，也不要提到 JSON。

只输出一个 JSON 对象，不要输出任何其他内容：
{"kind": "opening|followup|new_topic|closing", "topic": "本题主题（不超过 8 个字）", "question": "你要问的话"}
"""


@dataclass(slots=True)
class QuestionPlan:
    """面试官这一轮的意图：问什么、属于哪一类。"""

    question: str
    kind: str = "new_topic"
    topic: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_followup(self) -> bool:
        return self.kind == "followup"


def build_context(
    target: JobTarget | None,
    resume: Resume | None,
    *,
    max_questions: int,
    style: str,
) -> str:
    """把岗位、简历与场次参数压成一段上下文（放进 system 消息）。"""
    lines = [
        f"本场面试最多问 {max_questions} 个问题，问完即进入收尾。",
        f"面试风格：{style}。",
    ]
    if target is not None:
        label = f"{target.company or '（未填公司）'} · {target.title or '（未填岗位）'}"
        lines.append(f"目标岗位：{label}")
        if target.jd_text:
            lines.append("岗位 JD（节选）：\n" + target.jd_text[:1500])
    if resume is not None:
        variant = resume.variant_label or f"v{resume.version}"
        lines.append(f"候选人简历（{variant}，节选）：\n" + (resume.content_text or "")[:2000])
    return "\n".join(lines)


def build_messages(
    *,
    turns: Sequence[SessionTurn],
    context: str,
    closing: bool = False,
) -> list[LLMMessage]:
    """把已有回合转成对话消息：面试官→assistant，候选人→user。"""
    messages = [LLMMessage(role="system", content=BASE_SYSTEM_PROMPT + "\n\n【本场信息】\n" + context)]

    for turn in turns:
        if turn.speaker == "interviewer":
            messages.append(LLMMessage(role="assistant", content=turn.text))
        elif turn.speaker == "candidate":
            messages.append(LLMMessage(role="user", content=turn.text))

    if closing:
        messages.append(
            LLMMessage(
                role="user",
                content="（本轮是面试收尾：用一两句话收束整场面试，并询问候选人是否有想问的。kind 固定为 closing）",
            )
        )
    elif not turns:
        messages.append(LLMMessage(role="user", content="（现在开始面试。请先请候选人做自我介绍。）"))

    return messages


def parse_plan(result: LLMResult) -> QuestionPlan:
    """解析模型输出；解析失败时退化为"把整段文本当问题"，不让一场面试因为格式炸掉。"""
    try:
        data = extract_json(result.text)
    except ValueError:
        text = result.text.strip()
        return QuestionPlan(question=text, kind="new_topic", raw={"fallback": True})

    question = str(data.get("question") or "").strip() or result.text.strip()
    kind = str(data.get("kind") or "").strip() or "new_topic"
    return QuestionPlan(
        question=question,
        kind=kind,
        topic=str(data.get("topic") or "").strip(),
        raw=data,
    )


class Interviewer:
    """文字面试官。M1 起由 `realtime` 链路调用同一个实例。"""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def next_question(
        self,
        *,
        turns: Sequence[SessionTurn],
        target: JobTarget | None = None,
        resume: Resume | None = None,
        max_questions: int = 6,
        style: str = "标准技术面",
        closing: bool = False,
        policy_block: str = "",
        story_block: str = "",
        bank_block: str = "",
    ) -> tuple[QuestionPlan, LLMResult]:
        context = build_context(target, resume, max_questions=max_questions, style=style)
        if story_block.strip():
            # 事实底稿放在画像指令之前：先给事实，再给"怎么问"
            context = f"{context}\n\n{story_block.strip()}"
        if bank_block.strip():
            # 题库清单管「问什么」，policy 管「怎么问」。越靠近末尾的越容易被遵守，
            # 两者相比「怎么问」更该压轴，所以清单放在 policy 之前。
            context = f"{context}\n\n{bank_block.strip()}"
        if policy_block.strip():
            # 画像指令放最后：越靠近末尾的指令越容易被遵守（题型配比、追问深度都在这）
            context = f"{context}\n\n{policy_block.strip()}"
        messages = build_messages(turns=turns, context=context, closing=closing)
        result = await self.llm.complete(
            task="interview_turn",
            messages=messages,
            temperature=0.85,
            json_object=True,
        )
        return parse_plan(result), result
