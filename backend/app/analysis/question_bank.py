"""把题库选题变成面试官的指令块（M13）。

这里只做「把选中的题和提问要求拼成一段 prompt」，不查数据库——查库在
`vision_service.pick_questions`，与项目其他 `analysis/` 模块的分工一致。

**为什么要明确把题目清单写进 prompt**：让模型自由发挥时，题目会随对话漂移，
一场问完也说不清覆盖了哪些知识点。给出清单并要求按序提问之后，面试就有了
可对照的骨架；而追问仍然由模型根据回答生成。

> **顺序来自题库，深度来自模型。** 这是这个组合的关键——
> 顺序确定才谈得上「这场覆盖了哪些题」，追问灵活才不至于变成念题。

**要点也给模型**，但明确要求它别念出来：要点是给模型判断「候选人答没答到」
用的——答到了就往下走，没答到就追问。这比只给题干有效得多，而念出来就成了
泄露答案。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


def origin_of(source: str) -> str:
    """把 `source` 归一成来源标签（`builtin` / `uploaded` / `generated`）。

    **只在这一个地方定义。** 之前 `BankQuestion.origin` 与
    `vision_service._origin_of` 各写了一份，加了 `generated` 只改了一处，
    于是「实际抽到的来源分布」把模型生成的题全算成了内置题——
    测试抓到了这个不一致，所以合成一份。
    """
    if source.startswith("upload:"):
        return "uploaded"
    if source.startswith("generated:"):
        return "generated"
    return "builtin"


@dataclass(frozen=True, slots=True)
class BankQuestion:
    """从题库选中的一道题。

    `points` 是答题要点，供模型判断作答完整度；`source` 记住它来自哪个来源
    （内置 / 上传 / 模型生成），这样会话记录里能说清「这套题是怎么配出来的」。
    """

    id: int
    text: str
    points: list[str] = field(default_factory=list)
    category: str = ""
    source: str = ""
    """`builtin:vision`、`upload:<批次>` 或 `generated:<批次>`"""

    @property
    def origin(self) -> str:
        """归一化的来源标签，用于统计占比。"""
        return origin_of(self.source)


def bank_prompt_block(questions: Sequence[BankQuestion]) -> str:
    """拼出「本场题目清单」块。空列表返回空串，调用方据此跳过。"""
    if not questions:
        return ""

    lines: list[str] = [
        "【本场面试的题目清单】（按顺序提问）",
        "",
        f"本场要覆盖以下 {len(questions)} 道题，请**严格按顺序**提问，一次只问一题：",
        "",
    ]

    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question.text}")
        if question.points:
            hints = "；".join(question.points)
            lines.append(f"   （这个方向希望候选人谈到：{hints}）")

    lines.extend(
        [
            "",
            "提问规则：",
            "- 按 1、2、3……的顺序问下去，**不要跳题，也不要自己另出题目**。",
            "- 每道题可以结合候选人的回答追问 1~2 次；追问完就回到清单继续下一题。",
            "- 候选人明显答不上来时，最多追问 1 次就进入下一题，不要卡在同一题。",
            "- 清单问完后进入收尾环节（问候选人有什么想了解的）。",
            "- 括号里「希望候选人谈到」的内容是给你判断用的，**绝对不要念出来**，",
            "  也不要逐条提示——那是答不出时才需要你自己判断要不要追问的依据。",
        ]
    )

    return "\n".join(lines)
