"""Mock 谈判对手：按预算上限与每次让步幅度**真的拉锯**。

它不是随便回一句客套话：会读候选人最后一次报的数，再决定是"往上靠"还是"说明带宽不够"。
所以整场谈判会有真实的来回——这样谈判简报与守住率的计算才是在真数据上跑的。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ...analysis.negotiation import extract_amounts
from .base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage


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


def _wan(value: float) -> str:
    return f"{round(value / 10000, 1)} 万"


def _last_ask(candidate_turns: Sequence[str]) -> float | None:
    for text in reversed(list(candidate_turns)):
        amounts = extract_amounts(str(text))
        if amounts:
            # 取这一句里**最高**的那个数：候选人一句里常说"我期望 45 万，底线 40 万"
            return max(item.annual for item in amounts)
    return None


def build_mock_negotiation(messages: Sequence[LLMMessage]) -> str:
    record = _extract_record(messages)
    ceiling = float(record.get("ceiling") or 450_000)
    opening = float(record.get("opening") or min(ceiling * 0.8, ceiling - 40_000))
    step = float(record.get("step") or 20_000)
    round_index = int(record.get("round") or 0)
    candidate_turns = [str(item) for item in (record.get("candidate_turns") or [])]

    ask = _last_ask(candidate_turns)
    # 这一句里有没有报价：没有的话不该重复上一轮的结论，而要回应这个话题
    fresh_offer = bool(candidate_turns and extract_amounts(candidate_turns[-1]))

    if ask is None:
        if round_index <= 1:
            return (
                f"我们这个岗位的薪酬结构是月薪加年终，整体大概在 {_wan(opening)} 到 {_wan(ceiling)} 之间。"
                "你这边期望多少？"
            )
        # 对方迟迟不给数字时不重复开场白 —— 真实 HR 会直接把球踢回去
        return "你还没给具体数字，我这边走审批不好开口。你先说一个你能接受的数，我去争取。"

    if not fresh_offer:
        return "你说的这块我们可以聊。不过薪资我这边得有个具体数字才能去申请，你给个区间？"

    # 每回合往上抬一步，但绝不越过预算上限
    current = min(ceiling, opening + step * max(0, round_index))

    if ask <= current:
        return f"{_wan(ask)} 这个数我们可以接受，我这边走审批没问题。那我们就按这个方向定？"

    if ask <= ceiling:
        bumped = min(ceiling, max(current, ask - step / 2))
        return (
            f"你要的 {_wan(ask)} 我理解，但这个职级有带宽。"
            f"我尽量帮你争取到 {_wan(bumped)}，剩下的可以用签字费补一点。你看行吗？"
        )

    return (
        f"{_wan(ask)} 超出这个职级的带宽了，我最多能给到 {_wan(current)}。"
        "现金之外我能争取的是签字费和调薪节点，你看看这部分对你有没有价值。"
    )
