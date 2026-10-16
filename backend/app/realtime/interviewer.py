"""面试官编排（M0 文字版；M1 接入实时语音后，这段逻辑要被直接复用）。

职责边界很重要：这里只回答"问什么、怎么追问"，**不碰音频、不碰数据库**。
落库与事件下发交给 api/services 层——这样 M1 的 WebSocket 链路可以原样复用本模块，
而 M11 的压力模式/追问树也只是换 prompt 与策略参数。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..models.core import JobTarget, Resume
from ..models.session import SessionTurn
from ..providers.llm.base import LLMClient, LLMError, LLMMessage, LLMResult, extract_json

BASE_SYSTEM_PROMPT = """你是一位经验丰富、风格专业的技术面试官，正在对候选人进行模拟面试。

面试规则：
1. 每次只问一个问题，问题要具体、可回答；不要在一句话里塞多个问题。
2. 先听后问：必须结合候选人上一轮的回答追问，重点抓其中含糊、缺量化、缺取舍的地方。
3. 题型顺序：自我介绍 → 项目深挖 → 技术细节 → 行为与软技能 → 反问环节。
4. **要结合目标岗位 JD 和候选人的简历提问**：技术问题优先问 JD 里提到的方向、
   以及这个岗位实际会用到的能力；项目问题落到简历里写过的具体项目和技术选型上。
   不要问与岗位无关的通用题，也不要问「你了解 XX 吗」这种没有场景的问题。
5. **问题要有场景、有指向**：与其问「你了解卷积吗」，不如问「你这个项目里为什么用
   这个尺寸的卷积核，换成别的会有什么影响」。把问题锚在具体的取舍、边界和判断上——
   这是区分「背过」和「做过」的关键。
6. 像真人面试官一样说话：简洁、专业；不评价、不夸奖、不替候选人总结。
7. **可以先接一句很短的话再提问**，让它听起来像对话而不是问卷：例如「嗯」「好，我大致了解了」
   「明白」「这个点我想多问一句」。**最多 12 个字**，并且必须紧贴候选人刚说的内容——
   不要变成客套、不要重复他已经说过的话、也不要每轮都用同一句。
   候选人答得很短或明显没答到点时，**不要**接这句，直接追问。
8. 候选人跑题时，用一句话拉回来，然后重新提问。
9. 不要暴露你是 AI，不要提到"规则"，也不要提到 JSON。

只输出一个 JSON 对象，不要输出任何其他内容：
{"kind": "opening|followup|new_topic|closing", "topic": "本题主题（不超过 8 个字）", "question": "你要问的话"}
（当本场指令要求输出 coach 字段时，在同一个 JSON 对象里带上它即可。）
"""


#: 收尾时用的话术：**只是把话筒交给候选人**，不宣布面试结束。
#:
#: 以前的写法是「用一两句话收束整场面试」——模型于是说「面试到此结束」，
#: 而运行时紧接着就把会话标记成 finished，候选人**根本没有机会提问**。
#: 结果是评分 rubric 里的「主动性 / 反问质量」维度结构性拿不到数据。
CLOSING_PROMPT = (
    "（面试环节已经问完。请用一两句话感谢候选人，然后**邀请他提问**——"
    "问他有没有想了解的（团队、技术栈、业务、节奏都可以）。"
    "kind 固定为 closing。**不要说「面试结束」这类话**：接下来还有他提问的环节。）"
)

#: 回答候选人的提问。
REVERSE_QUESTION_PROMPT = (
    "（现在轮到候选人提问，他刚问了一个关于这份工作/团队/公司的问题。"
    "请**以面试官的身份直接回答**：具体、诚实、不夸大。"
    "可以提到你作为面试官能看到的信息（团队在做什么、技术栈、协作方式、面试流程与后续安排）；"
    "**不要编造**你不该知道的东西（比如具体薪资数字、公司内部机密），"
    "拿不准就说「这块 HR 会更清楚」。回答控制在三四句话，kind 固定为 closing。）"
)


@dataclass(slots=True)
class QuestionPlan:
    """面试官这一轮的意图：问什么、属于哪一类。"""

    question: str
    kind: str = "new_topic"
    topic: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    #: M14 教练模式：上一题的即时点评（{"points","missed","better"}），无则 None。
    #: 随回合 meta 落库，前端展示"教练点评"卡。
    coach: dict[str, str] | None = None

    @property
    def is_followup(self) -> bool:
        return self.kind == "followup"


#: 空返回 / 瞬时失败时的总尝试次数与退避秒数（下标 = 第几次失败后的等待）。
#: 模型偶尔会把全部输出花在"思考"上导致 content 为空，或服务端 5xx / 限流
#: 抖动——这些重试一次基本就回来了；4xx（Key / 模型名配错）不重试，直接报错。
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 2.0)


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
    interrupted: bool = False,
    force_scene: bool = False,
    prompt: str = "",
) -> list[LLMMessage]:
    """把已有回合转成对话消息：面试官→assistant，候选人→user。"""
    messages = [LLMMessage(role="system", content=BASE_SYSTEM_PROMPT + "\n\n【本场信息】\n" + context)]

    for turn in turns:
        if turn.speaker == "interviewer":
            messages.append(LLMMessage(role="assistant", content=turn.text))
        elif turn.speaker == "candidate":
            messages.append(LLMMessage(role="user", content=turn.text))

    if prompt.strip():
        # 显式指令优先：调用方知道这一轮该做什么（收尾 / 回答候选人的提问）
        messages.append(LLMMessage(role="user", content=prompt.strip()))
    elif closing:
        messages.append(
            LLMMessage(
                role="user",
                content="（本轮是面试收尾：用一两句话收束整场面试，并询问候选人是否有想问的。kind 固定为 closing）",
            )
        )
    elif force_scene:
        # M14 现场突发：候选人数轮数不可靠（实测会被追问带跑），
        # 这里在编排层精确卡住"第 3 题之后"的时机，用末尾 user 指令强制执行。
        messages.append(
            LLMMessage(
                role="user",
                content="（本场启用了现场突发：候选人已经回答了 3 道题，**这一问必须**是一道"
                "现场突发情境题——设备突然故障 / 客户现场发难 / 节拍不够 / 环境干扰等，"
                "用「假设现在在现场，…」这类假设开头，kind 固定为 scene，"
                "要求候选人当场给出排查步骤与取舍。不要追问上一题，也不要继续深挖。）",
            )
        )
    elif interrupted:
        # M14 打断续问：上一轮被面试官插话打断，回答只到一半。
        # 下一问必须紧贴最后一句继续追，而不是换个话题重新问。
        messages.append(
            LLMMessage(
                role="user",
                content="（上一轮面试官插话打断了候选人。请**紧贴候选人上一条回答的最后一句**"
                "继续追问同一个话题——就像真人面试官插话打断后，回头接着他说的半句话往下问。"
                "不要跳到别的话题，也不要从头把问题再念一遍。）",
            )
        )
    elif not turns:
        messages.append(LLMMessage(role="user", content="（现在开始面试。请先请候选人做自我介绍。）"))

    return messages


def parse_plan(result: LLMResult) -> QuestionPlan:
    """解析模型输出；解析失败时退化为"把整段文本当问题"，不让一场面试因为格式炸掉。

    唯一不降级的底线：**最终问题不能为空**。空问题落库就是"面试官思考完什么都没说"
    的直接来源（用户在界面上看到思考指示消失后只有空白）。所以这里抛 LLMError，
    由 `next_question` 的重试兜底，而不是把空文本继续往上送。
    """
    try:
        data = extract_json(result.text)
    except ValueError:
        text = result.text.strip()
        if not text:
            raise LLMError("模型返回了空内容（解析不出 JSON，也没有可用的纯文本）")
        return QuestionPlan(question=text, kind="new_topic", raw={"fallback": True})

    # JSON 解析出来了但 question 为空：不能拿原始 JSON 当问题问出去——那会把
    # 模型输出原样当话术，用户看到一条 JSON 串。按失败处理，交给上层重试。
    question = str(data.get("question") or "").strip()
    if not question:
        raise LLMError("模型返回了空问题（JSON 的 question 字段为空）")
    kind = str(data.get("kind") or "").strip() or "new_topic"
    raw_coach = data.get("coach")
    coach: dict[str, str] | None = None
    if isinstance(raw_coach, dict):
        # 教练点评是纯文本三件套；值不是字符串的条目直接丢弃
        coach = {
            key: str(value).strip()
            for key, value in raw_coach.items()
            if key in ("points", "missed", "better") and str(value).strip()
        }
        if not coach:
            coach = None
    return QuestionPlan(
        question=question,
        kind=kind,
        topic=str(data.get("topic") or "").strip(),
        raw=data,
        coach=coach,
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
        interrupted: bool = False,
        force_scene: bool = False,
        prompt: str = "",
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
        messages = build_messages(
            turns=turns,
            context=context,
            closing=closing,
            interrupted=interrupted,
            force_scene=force_scene,
            prompt=prompt,
        )
        # 空返回体与瞬时失败（超时 / 5xx / 限流）自动重试：模型偶尔会把全部输出
        # 花在"思考"上、或服务端抖动，重试一次基本就回来了。4xx（Key / 模型名错）
        # 不重试，直接抛给调用方——那属于配置问题，重试只会白等。
        #
        # **重试不是原样重发**：空 content 的头号原因是模型把 token 全花在思考上，
        # 原样重发大概率还是空（历史会话 28 正是连空 3 次、面试卡死）。从第二次
        # 尝试起压低温度并追加"直接输出"指令，逼模型跳过思考直接给题。
        last_error: LLMError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                attempt_messages = messages
                temperature = 0.85
                if attempt > 0:
                    attempt_messages = [
                        *messages,
                        LLMMessage(
                            role="user",
                            content=(
                                "注意：上一步你没有输出题目内容。现在请直接输出题目："
                                "不要任何思考过程、不要解释、不要 reasoning，"
                                "只输出要求的 JSON。"
                            ),
                        ),
                    ]
                    temperature = 0.3
                result = await self.llm.complete(
                    task="interview_turn",
                    messages=attempt_messages,
                    temperature=temperature,
                    json_object=True,
                )
                plan = parse_plan(result)
                return plan, result
            except LLMError as exc:
                last_error = exc
                if not exc.retryable or attempt >= len(RETRY_BACKOFF_SECONDS):
                    raise
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
        raise last_error or LLMError("面试官生成问题失败")
