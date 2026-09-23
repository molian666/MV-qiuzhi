"""薪资谈判：战术库 + 谈判简报（纯函数）+ HR 角色的 prompt。

**简报为什么用纯函数算**：("守住底线没有""让步是不是太快")这些都是**能从对话里量出来的**。
交给模型评会得到一个不可复现、也无法解释的数字；用纯函数算，
每个结论都能指向具体哪一句、哪个数。模型只负责两件事：演 HR、写主观点评。

**金额归一化的坑**：中文谈判里"35k""3.5 万""35 万""35000"都可能出现，
而且"35 万"既可能是月薪（不可能）也可能是年薪（正常）。
所以这里统一归一到**年薪**，并按上下文词（月薪/年薪）与数量级双重判断——
这是整个简报准确性的地基。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..providers.llm.base import RECORD_MARKER_END, RECORD_MARKER_START, LLMMessage

# --------------------------------------------------------------------------- #
# 战术
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Tactic:
    key: str
    label: str
    summary: str
    when: str
    #: 识别线索：候选人说了这些词就认为用了这一招
    cues: tuple[str, ...]


TACTICS: dict[str, Tactic] = {
    "ask_range": Tactic(
        key="ask_range",
        label="先反问预算",
        summary="先问对方的薪资区间，别自己先报数",
        when="开场第一步。先出价的一方容易被锚定在低位。",
        cues=("薪资区间", "薪资范围", "预算", "区间是多少", "大概是什么区间", "先了解一下预算", "能给到多少"),
    ),
    "market_data": Tactic(
        key="market_data",
        label="摆市场行情",
        summary="用同城同岗位的市场区间做依据",
        when="对方压价、或对方说「我们预算有限」时。",
        cues=("市场行情", "同行", "同城", "招聘网站", "行业水平", "市面上", "这个级别普遍"),
    ),
    "competing_offer": Tactic(
        key="competing_offer",
        label="并列 offer",
        summary="有其他 offer 时用它抬价（但**不能说谎**）",
        when="手里确实有别的 offer 时。编造一旦被追问细节就会崩。",
        cues=("另一个 offer", "另外一家", "别家给", "手里还有", "同时在谈"),
    ),
    "total_package": Tactic(
        key="total_package",
        label="谈总包",
        summary="月薪谈不动时转向签字费、股票、职级",
        when="对方在月薪上有硬上限（职级薪酬带宽）时。",
        cues=("签字费", "股票", "期权", "总包", "年包", "职级", "package", "年终"),
    ),
    "hold_bottom_line": Tactic(
        key="hold_bottom_line",
        label="守底线",
        summary="低于底线就明确说无法接受",
        when="报价已经触到你的底线时。含糊会被直接读成「还能再让」。",
        cues=("低于", "不能接受", "无法接受", "这是我的底线", "低于这个数就不考虑"),
    ),
    "non_salary": Tactic(
        key="non_salary",
        label="换非现金项",
        summary="谈远程、年假、职级、培训、调薪节点",
        when="现金空间确实没了的时候。这些常常比再挤 1000 块月薪更有价值。",
        cues=("远程", "年假", "弹性", "调薪", "培训", "职级", "汇报关系", "转岗"),
    ),
    "anchor_high": Tactic(
        key="anchor_high",
        label="高位锚定",
        summary="报一个略高于目标的数字，留出让步空间",
        when="你已经知道区间、要给出自己的期望时。",
        cues=("我期望", "希望是", "我的目标", "期望薪资", "理想是"),
    ),
    "walk_away": Tactic(
        key="walk_away",
        label="礼貌离场",
        summary="差距过大时体面地中止，而不是硬撑",
        when="对方的上限明显低于你的底线时。硬撑只会消耗彼此的耐心。",
        cues=("那可能不太合适", "先这样", "感谢", "以后再合作", "这次先不打扰"),
    ),
}

TACTIC_ORDER: tuple[str, ...] = tuple(TACTICS.keys())


# --------------------------------------------------------------------------- #
# 金额归一化
# --------------------------------------------------------------------------- #
_MONTHLY_CUES = ("月薪", "每月", "一个月", "税前月", "base", "月 base", "月度")
_ANNUAL_CUES = ("年薪", "年包", "总包", "一年", "年度", "package", "年收入")

_K_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*[kK](?![a-zA-Z])")
_WAN_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*万")
_PLAIN_PATTERN = re.compile(r"(?<![\d.])(\d{4,7})(?![\d.])")

#: 超过这个金额就默认按"年薪"理解（月薪 10 万以上属于极少数）
ANNUAL_THRESHOLD = 100_000.0

#: 线索词与金额之间的最大字距。超过这个距离就不算线索——
#: 「我们这个岗位的薪酬结构是月薪加年终，整体大概在 36.0 万」里的「月薪」离数字 10 个字，
#: 不能拿它把 36 万判成月薪（实测让"最终报价"差了 12 倍）。
#: 定 6 是个安全的紧度：常见写法「月薪 3.5 万」「月薪是 3.5 万」都在 3 个字以内，
#: 而超出它的极端写法会落进"按数量级判断"的兜底逻辑，结果同样正确。
MAX_CUE_DISTANCE = 6


@dataclass(slots=True)
class AmountRef:
    raw: str
    #: 归一化后的**年薪**（元）
    annual: float
    kind: str  # monthly | annual
    #: 在原文里的字符位置（用于还原对话顺序）
    position: int = 0


def _nearest_distance(text: str, start: int, end: int, cues: Sequence[str]) -> int | None:
    """线索词与本段金额的最近字距。

    用**最近距离**而不是"窗口内出现过"：像「月薪 3.5 万，年薪 42 万」这种句子，
    固定窗口会把两个线索都圈进来，于是 42 万被误判成月薪（实测踩到过）。
    """
    best: int | None = None
    for cue in cues:
        position = text.find(cue)
        while position != -1:
            if position + len(cue) <= start:
                distance = start - (position + len(cue))
            elif position >= end:
                distance = position - end
            else:
                distance = 0
            if best is None or distance < best:
                best = distance
            position = text.find(cue, position + 1)

    # 太远的线索不算线索（见 MAX_CUE_DISTANCE 的说明）
    if best is not None and best > MAX_CUE_DISTANCE:
        return None
    return best


def _classify(value: float, text: str, start: int, end: int, *, default_kind: str | None) -> str:
    monthly = _nearest_distance(text, start, end, _MONTHLY_CUES)
    annual = _nearest_distance(text, start, end, _ANNUAL_CUES)

    if monthly is not None or annual is not None:
        if monthly is None:
            return "annual"
        if annual is None:
            return "monthly"
        return "monthly" if monthly <= annual else "annual"

    if default_kind is not None:
        return default_kind
    return "annual" if value >= ANNUAL_THRESHOLD else "monthly"


def extract_amounts(text: str) -> list[AmountRef]:
    """把一段话里的薪资数字抽出来，统一归一到**年薪**。

    识别顺序：`35k` → `3.5 万` → 裸数字（35000）。`35k` 在中文语境里基本一定是月薪，
    所以它单独给一个默认档；其余靠上下文词与数量级判断。
    """
    content = text or ""
    refs: list[AmountRef] = []
    seen_spans: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < other_end and end > other_start for other_start, other_end in seen_spans)

    for pattern, multiplier, default_kind in (
        (_K_PATTERN, 1_000.0, "monthly"),
        (_WAN_PATTERN, 10_000.0, None),
        (_PLAIN_PATTERN, 1.0, None),
    ):
        for match in pattern.finditer(content):
            start, end = match.span()
            if overlaps(start, end):
                continue
            value = float(match.group(1)) * multiplier
            if value <= 0:
                continue
            kind = _classify(value, content, start, end, default_kind=default_kind)
            annual = value * 12 if kind == "monthly" else value
            refs.append(AmountRef(raw=match.group(0), annual=round(annual, 2), kind=kind, position=start))
            seen_spans.append((start, end))

    # 按**出现位置**排序（= 对话顺序），不是按金额：
    # 调用方要靠顺序判断「第一次报价」与「最后一次报价」，
    # 按金额排会把这个信息直接抹掉（这个 bug 实测抓到过）。
    refs.sort(key=lambda item: item.position)
    return refs


# --------------------------------------------------------------------------- #
# 简报
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class BriefTurn:
    speaker: str
    text: str


@dataclass(slots=True)
class NegotiationBrief:
    rounds: int = 0
    bottom_line: float = 0.0
    target: float = 0.0
    candidate_quotes: list[float] = field(default_factory=list)
    interviewer_quotes: list[float] = field(default_factory=list)
    first_ask: float | None = None
    last_ask: float | None = None
    final_offer: float | None = None
    bottom_line_held: bool | None = None
    concession_ratio: float = 0.0
    gap_to_target: float | None = None
    tactics_used: list[str] = field(default_factory=list)
    tactics_missed: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    score: float = 0.0
    summary: str = ""
    caveat: str = ""


def _amounts_from(text: str) -> list[float]:
    return [item.annual for item in extract_amounts(text)]


def build_brief(
    turns: Sequence[BriefTurn | dict[str, Any]],
    *,
    bottom_line: float,
    target: float,
) -> NegotiationBrief:
    """从整场谈判对话里算出简报。

    所有结论都指向具体数字或具体用词，方便你自己回看那句话。
    """
    brief = NegotiationBrief(bottom_line=float(bottom_line or 0), target=float(target or 0))

    candidate_chunks: list[str] = []
    interviewer_chunks: list[str] = []
    for turn in turns:
        speaker = turn.get("speaker", "") if isinstance(turn, dict) else turn.speaker
        text = turn.get("text", "") if isinstance(turn, dict) else turn.text
        if not str(text).strip():
            continue
        if speaker == "candidate":
            candidate_chunks.append(str(text))
        else:
            interviewer_chunks.append(str(text))

    brief.rounds = len(candidate_chunks)
    candidate_text = "\n".join(candidate_chunks)
    interviewer_text = "\n".join(interviewer_chunks)

    brief.candidate_quotes = _amounts_from(candidate_text)
    brief.interviewer_quotes = _amounts_from(interviewer_text)

    if brief.candidate_quotes:
        brief.first_ask = brief.candidate_quotes[0]
        brief.last_ask = brief.candidate_quotes[-1]
    if brief.interviewer_quotes:
        # 用**最高**的那个：HR 的最终报价通常是全程最高的数字，
        # 取最后一个容易把"我们再研究一下（没给数）"之类的寒暄误当成最终报价
        brief.final_offer = max(brief.interviewer_quotes)

    # 守住底线看的是**你最后接受的数**，不是 HR 报过的最高数：
    # 「我最多能给到 52 万」是上限声明，不等于成交价；只看它就会把
    # 「你自己松口到 45 万」这件事漏掉（这个 bug 被测试抓到了）。
    if brief.last_ask is not None and brief.bottom_line > 0:
        brief.bottom_line_held = brief.last_ask >= brief.bottom_line

    reference = max([value for value in (brief.last_ask, brief.final_offer) if value], default=None)
    if reference is not None and brief.target > 0:
        brief.gap_to_target = round(reference - brief.target, 2)

    if brief.first_ask and brief.last_ask and brief.first_ask > brief.last_ask:
        space = max(1.0, (brief.target or brief.first_ask) - brief.bottom_line)
        brief.concession_ratio = round((brief.first_ask - brief.last_ask) / space, 3)

    for key, tactic in TACTICS.items():
        if any(cue in candidate_text for cue in tactic.cues):
            brief.tactics_used.append(key)
        else:
            brief.tactics_missed.append(key)

    brief.issues = _issues(brief, candidate_text=candidate_text)
    brief.score = _score(brief)
    brief.summary = _summary(brief)
    brief.caveat = (
        "这里只根据对话里出现的数字与用词做判断，**不评价对方的报价是否合理**——"
        "同城同岗位的行情需要你自己去核实。"
    )
    return brief


def _issues(brief: NegotiationBrief, *, candidate_text: str) -> list[str]:
    issues: list[str] = []

    if not brief.candidate_quotes:
        issues.append("整场谈判里你没报过任何数字——没有具体数字，对方只能按最低档给你。")
        return issues

    if brief.bottom_line > 0 and brief.first_ask is not None and brief.first_ask <= brief.bottom_line * 1.05:
        issues.append(
            f"你第一次报的 {round(brief.first_ask / 10000, 1)} 万几乎贴着底线，后面几乎没有让步空间。"
        )

    if "ask_range" not in brief.tactics_used:
        issues.append("你没有先问对方的预算区间就直接报了数——先出价的一方容易被锚定在低位。")

    if brief.concession_ratio > 0.4:
        issues.append(
            f"你的让步幅度达到报价空间的 {round(brief.concession_ratio * 100)}%：让得太快，"
            "对方会认为还有空间。比较稳的节奏是每次只让一小步，并且要对方也给一点。"
        )

    if brief.target > 0 and brief.final_offer is not None and brief.final_offer < brief.target * 0.9:
        issues.append(
            f"对方的最终数（{round(brief.final_offer / 10000, 1)} 万）离你的目标还差得较多，"
            "这时候该考虑换非现金项，或者礼貌止损。"
        )

    if brief.bottom_line_held is False and "hold_bottom_line" not in brief.tactics_used:
        issues.append("对方给到了底线以下，但你没有明确表示无法接受——含糊会被读成「还能再让」。")

    if "total_package" not in brief.tactics_used and "non_salary" not in brief.tactics_used:
        issues.append("你全程只在谈月薪。月薪谈不动时，签字费、股票、年假、远程往往更好谈。")

    return issues


def _score(brief: NegotiationBrief) -> float:
    """0–100：底线守住 + 不轻易让步 + 用到关键战术。"""
    score = 70.0

    if brief.bottom_line_held is True:
        score += 20
    elif brief.bottom_line_held is False:
        score -= 25

    if brief.concession_ratio <= 0.25:
        score += 10
    elif brief.concession_ratio > 0.4:
        score -= 15

    for key in ("ask_range", "hold_bottom_line", "total_package"):
        if key in brief.tactics_used:
            score += 5

    score -= len(brief.issues) * 4
    return round(max(0.0, min(100.0, score)), 1)


def _summary(brief: NegotiationBrief) -> str:
    parts: list[str] = []
    if brief.candidate_quotes:
        parts.append(f"你出价 {len(brief.candidate_quotes)} 次")
    if brief.final_offer is not None:
        parts.append(f"对方最终给到 {round(brief.final_offer / 10000, 1)} 万/年")
    if brief.bottom_line_held is True:
        parts.append("底线守住了")
    elif brief.bottom_line_held is False:
        parts.append("**低于你的底线**")
    if brief.tactics_used:
        used = "、".join(TACTICS[key].label for key in brief.tactics_used[:3])
        parts.append(f"用到的战术：{used}")
    return "；".join(parts) if parts else "这场谈判没什么可分析的（没有出现薪资数字）。"


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
NEGOTIATION_PROMPT = """你在一场**薪资谈判**里扮演用人方的 HR/招聘负责人。

你的处境（必须一致，不要前后矛盾）：
- 这个岗位的预算上限是 {ceiling}（年薪，元）。超过这个数字你没有权限批。
- 你的初始报价是 {opening}（年薪，元）。
- 你希望尽快谈成，但**不会一次让到位**：每次最多让 {step} 元，并且要让对方也给出理由。
- 你可以用这些说辞：职级带宽有限、同级别同事的薪酬要公平、绩效部分有想象空间、
  可以争取签字费/股票/调薪节点。

对话规则：
1. 全程中文，像真人 HR 一样说话（可以寒暄、可以反问、可以施压）。
2. **不要一次给到上限**；对方没给出理由时，就把话题引回"你的期望依据是什么"。
3. 对方要求超过上限时，明确说明权限边界，并把话题转到非现金项。
4. 每轮回复 2–4 句，不要长篇大论。
5. 只输出你要说的话，不要任何解释或标注。
"""


def build_reply_messages(
    *,
    candidate_turns: Sequence[str],
    history: Sequence[dict[str, str]],
    ceiling: float,
    opening: float,
    step: float,
) -> list[LLMMessage]:
    system = NEGOTIATION_PROMPT.format(
        ceiling=f"{round(ceiling)}",
        opening=f"{round(opening)}",
        step=f"{round(step)}",
    )
    payload = {
        "ceiling": round(ceiling),
        "opening": round(opening),
        "step": round(step),
        "round": len(history),
        "candidate_turns": list(candidate_turns)[-6:],
    }
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(
            role="user",
            content=(
                f"{RECORD_MARKER_START}\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n{RECORD_MARKER_END}\n\n"
                "请以 HR 的身份回复候选人的最后一句话。"
            ),
        ),
    ]


def build_opening_messages(*, company: str, title: str, script: str = "") -> list[LLMMessage]:
    payload = {"company": company, "title": title, "script": script}
    return [
        LLMMessage(
            role="system",
            content=(
                "你在一场薪资谈判里扮演用人方的 HR 负责人。这是谈判的第一句话："
                "简短开场，说明这个岗位的薪资结构（月薪 × 月数 + 年终 + 股票），"
                "然后**先问候选人的期望**。不要说空话，2–3 句即可，全程中文。"
            ),
        ),
        LLMMessage(
            role="user",
            content=f"{RECORD_MARKER_START}\n{payload}\n{RECORD_MARKER_END}\n请说出你的开场白。",
        ),
    ]


def tactic_options() -> list[dict[str, Any]]:
    return [
        {"key": tactic.key, "label": tactic.label, "summary": tactic.summary, "when": tactic.when}
        for tactic in TACTICS.values()
    ]
