"""节奏学习：把一段真实面试的对话变成"面试官画像"，再把画像变成模拟时的运行时策略。

三层结构，**每一层都是纯函数**（可单测、可离线复算）：

    parse_transcript  → 对话文稿 → 带说话人与时间的段落
    split_questions   → 段落 → 问题单元（含追问链、等待时长、打断标记）
    compute_features  → 问题单元 → 量化特征（语速/等待/追问深度/打断率/题型分布…）
    build_params      → 特征 + 打标 → interviewer_profile.params
    build_policy      → params → PacingPolicy（模拟时真正生效的东西）

关于"没有时间戳怎么办"：用户粘贴的文稿通常没有时间轴。这时用字数推算
（中文面试语速 ≈ 4.2 字/秒），并在 `features.sources` 里标明哪些字段是 measured、
哪些是 estimated —— 宁可在页面上写"等待时长按字数估算"，也不要假装测准了。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Sequence

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: 中文面试语速的经验值（字/秒），用于没有时间戳时推算时长
CHARS_PER_SECOND = 4.2
MIN_SEGMENT_MS = 300
DEFAULT_WAIT_MS = 1200
DEFAULT_FOLLOWUP_DEPTH = 2

TOPIC_LABELS: dict[str, str] = {
    "self_intro": "自我介绍",
    "project_deep": "项目深挖",
    "tech": "技术细节",
    "behavior": "行为面试",
    "algorithm": "算法题",
    "scenario": "场景题",
    "salary": "薪资与期望",
    "reverse": "反问环节",
    "general": "通用问题",
    "followup": "追问",
}

#: 真正的**题型**键（模型给每道题打的 `type`）。
#: 与"问题结构"键（`new_topic` / `opening` / `closing`，来自 `QuestionUnit.kind`）
#: 区分开：结构键没有中文标签，混进"题型配比"会以英文漏进 prompt，
#: 而且它描述的是"这是新话题还是追问"，跟"问什么内容"不是一回事。
QUESTION_TYPE_KEYS: frozenset[str] = frozenset(
    {"self_intro", "project_deep", "tech", "behavior", "algorithm", "scenario", "salary", "reverse", "general"}
)

QUESTION_MARKERS = (
    "？",
    "?",
    "吗",
    "呢",
    "什么",
    "怎么",
    "如何",
    "为什么",
    "能不能",
    "有没有",
    "哪",
    "多少",
    "介绍",
    "说说",
    "讲讲",
    "谈一谈",
    "举个例子",
)

FOLLOWUP_MARKERS = ("那", "那么", "具体", "再", "还有", "继续", "比如", "当时", "为什么")

CLOSING_MARKERS = ("你有什么想问", "反问", "薪资", "期望薪资", "待遇", "还有什么想了解")

_TIMESTAMP = re.compile(r"^\s*[\[\(]?(\d{1,2}):(\d{2})(?:[.:](\d{1,2}))?[\]\)]?\s*")

#: 说话人标记。分两类，区别在**要不要强制分隔符**——
#: 中文没有词边界，宽松匹配会把普通句子吃成"标记 + 内容"：
#: 「我是 5 年视觉工程师」曾被判成说话人 `我` + 内容 `是 5 年视觉工程师`，
#: 于是没标说话人的文稿被**静默错切**（比丢掉更糟：错的东西看起来是对的）。
#: 所以单字/单字母的标记（我 / 答 / q / A）后面**必须有** `:`、`：` 或成对括号；
#: 而「面试官」「候选人」这类多字词本身够独特，冒号可以省。
_SPEAKER_STRICT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*[\[\(【]\s*(我|答|a|A|q|Q)\s*[\]\)】]\s*[:：]?\s*(.+)$"), "candidate"),
    (re.compile(r"^\s*(我|答|a|A)\s*[:：]\s*(.+)$"), "candidate"),
    (re.compile(r"^\s*[\[\(【]\s*(面试官\d*|hr|HR|Hr|q|Q)\s*[\]\)】]\s*[:：]?\s*(.+)$"), "interviewer"),
    (re.compile(r"^\s*(面试官\d*|hr|HR|Hr|q|Q)\s*[:：]\s*(.+)$"), "interviewer"),
)

_SPEAKER_LOOSE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*[\[\(【]?\s*(面试官\d*)\s*[\]\)】]?\s*[:：]?\s*(.+)$"), "interviewer"),
    (re.compile(r"^\s*[\[\(【]?\s*(候选人|本人|求职者)\s*[\]\)】]?\s*[:：]?\s*(.+)$"), "candidate"),
)

#: 「发言人1 / 说话人2 / Speaker A」这类**只标编号、不含角色**的说话人。
#: 关键点：这里**保留编号原样**（不映射成 interviewer/candidate）——
#: 谁是面试官要靠 `infer_speaker_roles` 按内容判断，此时还不能下结论。
#: 如果这里就草率地把"发言人1"当成面试官，遇到反过来的录音就会全盘搞反。
#:
#: 时间戳在**冒号之前**：`_merge_speaker_headers` 产出的是
#: `00:10 发言人1：正文`（时间戳放行首，才会被 `_parse_timestamp` 吃掉）。
_GENERIC_SPEAKER = re.compile(
    r"^\s*(发言人\s*\d+|说话人\s*\d+|讲话人\s*\d+|Speaker\s*[A-Za-z0-9]+)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)

_SPEAKER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = _SPEAKER_STRICT + _SPEAKER_LOOSE

#: 转写软件导出时前面那几行：日期（几年前的都算）与"…面试_原文"这类标题
_DATE_LINE = re.compile(
    r"^\s*\d{4}\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?\s*(\d{1,2}:\d{2}(:\d{2})?)?\s*$"
)
_HEADER_MARKERS = ("面试_原文", "面试原文", "转写", "录音原文", "对话原文", "字幕")

#: 「<名称>_原文」这种**会议软件导出标题**。
#:
#: 腾讯会议、飞书妙记等导出的是「方亿荣 华海达面试_原文」这种，
#: 但只写公司名/人名时是「深圳杰美康_原文」——**不含"面试"二字**，
#: 于是 `_HEADER_MARKERS` 漏掉了它。而这行漏掉之后，后面所有头部检查
#: 都会因为 `not drafts` 失效（见 `parse_transcript` 里的注释）。
_HEADER_TITLE_LINE = re.compile(r"^.{0,28}[_\-—]?\s*原文\s*$")

#: 「只有说话人标签（+可选时间戳）、没有正文」的独立一行。
#: 转写软件（腾讯会议 / 飞书妙记等）复制出来就是这种：
#:
#:     发言人1   00:10
#:     是做视觉工程师是吧？那你先介绍一下工作经历。
#:
#: 这类标签**不含角色语义**（发言人1/2 谁都有可能是面试官），
#: 所以先由 `_merge_speaker_headers` 与下一行合并，再交给 `infer_speaker_roles`
#: 按"谁在提问"判断角色。
_SPEAKER_ONLY = re.compile(
    r"^\s*(发言人\s*\d+|说话人\s*\d+|讲话人\s*\d+|Speaker\s*[A-Za-z0-9]+|"
    r"受访者\s*\d*|面试官\s*\d*|候选人\s*\d*|客户\s*\d*|客服\s*\d*)"
    r"\s*[:：]?\s*"
    r"(\d{1,2}:\d{2}(?:[.:]\d{1,2})?)?"
    r"\s*$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# 第一层：文稿 → 段落
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SegmentDraft:
    idx: int
    speaker: str  # interviewer | candidate | unknown
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    #: 原文里的原始说话人标签（如「发言人1」）。角色是推断出来的时候，
    #: 必须把这个一起给用户看——否则他无法验证"面试官到底是不是发言人1"。
    source_label: str = ""


def _parse_timestamp(line: str) -> tuple[int | None, str]:
    match = _TIMESTAMP.match(line)
    if not match:
        return None, line
    minutes, seconds, fraction = match.groups()
    value = (int(minutes) * 60 + int(seconds)) * 1000
    if fraction:
        value += int(fraction.ljust(2, "0")[:2]) * 10
    return value, line[match.end() :].strip()


def _estimate_ms(text: str, *, chars_per_second: float = CHARS_PER_SECOND) -> int:
    return max(MIN_SEGMENT_MS, int(len(text) / max(0.5, chars_per_second) * 1000))


def _looks_like_header(line: str) -> bool:
    """判断一行是不是"文件头"（标题 / 导出时间），而不是对话内容。

    只在**还没有任何段落**时才会被调用，所以误判的代价很小；
    但判错的后果不小（会吃掉第一句话），因此规则写得很紧：
    必须形如日期，或"很短 + 含面试/原文/转写"这类标题特征。
    """
    text = line.strip()
    if not text:
        return False
    # 日期行：2026年09月24日 / 2026-09-24 / 2026/09/24 / 2026.09.24
    if _DATE_LINE.match(text):
        return True
    # 会议软件导出的标题行：「深圳杰美康_原文」「华海达面试_原文」
    if _HEADER_TITLE_LINE.match(text):
        return True
    # 标题行：短，且带明显的"这是份记录"的痕迹
    if len(text) <= 30 and any(marker in text for marker in _HEADER_MARKERS):
        return True
    return False


def parse_transcript(
    text: str,
    *,
    chars_per_second: float = CHARS_PER_SECOND,
    roles_info: list[str] | None = None,
) -> list[SegmentDraft]:
    """解析对话文稿。支持这些写法（可以混用）：

        [面试官] 先做个自我介绍吧
        00:12 面试官：你简历里这个项目讲一下
        我：我做的是订单系统
        Q: 为什么离职
        A: 因为想换方向

    也支持**转写软件导出的"发言人 + 独立时间行"格式**（腾讯会议/飞书妙记等
    直接复制出来就是这种）：

        发言人1   00:10
        是做视觉工程师是吧？那你先介绍一下工作经历。

        发言人2   00:21
        可以看到吗？

    这种格式里说话人叫「发言人1/2」，**不带任何角色语义**，所以解析完还要靠
    `infer_speaker_roles` 按"谁在提问"判断谁是面试官——否则整份文稿切不出问答。

    `roles_info`：传入一个 list 时，会把"角色是推断出来的"说明追加进去
    （调用方负责展示，让用户知道这是猜测、猜错时怎么改）。

    头部行：转写软件导出时前面会带文件标题与导出时间，它们不是对话。
    头部典型是 1–2 行，所以只在**最前面几行**里做这个判断；用行数上限而不是
    "必须是第一行"，是因为后者太脆——真实的「深圳杰美康_原文」漏过一次，
    连带日期行一起放进了正文（`_HEADER_TITLE_LINE` 就是为此补的）。
    """
    lines = _merge_speaker_headers((text or "").splitlines())

    drafts: list[SegmentDraft] = []
    HEADER_SCAN_LINES = 3

    for line_index, line in enumerate(lines):
        # 转写软件导出时前面会带文件标题与导出时间（`方亿荣 华海达面试_原文` /
        # `2026年09月24日 09:11`）。它们不是对话，留着会被算进第一个人的发言，
        # 还会把时间轴起点拉到 0（于是第一题的"等待时长"凭空变成 10 秒）。
        if line_index < HEADER_SCAN_LINES and _looks_like_header(line):
            continue

        start_ms, line = _parse_timestamp(line)
        if not line:
            continue

        speaker = "unknown"
        content = line
        source_label = ""
        for pattern, name in _SPEAKER_PATTERNS:
            match = pattern.match(line)
            if match:
                speaker = name
                content = match.group(2).strip()
                break
        else:
            # 只标编号的说话人（发言人1/2…）：保留编号，交给 infer_speaker_roles 判角色
            generic = _GENERIC_SPEAKER.match(line)
            if generic:
                source_label = re.sub(r"\s+", "", generic.group(1))
                speaker = source_label
                content = generic.group(2).strip()

        if not content:
            continue

        drafts.append(
            SegmentDraft(
                idx=len(drafts),
                speaker=speaker,
                text=content,
                start_ms=start_ms,
                source_label=source_label,
            )
        )

    _fill_timestamps(drafts, chars_per_second=chars_per_second)
    note = infer_speaker_roles(drafts)
    if note and roles_info is not None:
        roles_info.append(note)
    return drafts


def _merge_speaker_headers(lines: Sequence[str]) -> list[str]:
    """把「说话人一行 + 正文一行」合并成 `说话人：正文`。

    转写软件常把说话人和时间单独占一行，正文在下一行：

        发言人1   00:10
        是做视觉工程师是吧？

    不合并的话，`发言人1   00:10` 会被当成一句正文（而且时间戳也吃不到），
    整份文稿就废了。只在"这一行**只有**说话人标签与时间、且下一行不是新的说话人"
    时才合并，避免把正常对话误吞掉。
    """
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue

        match = _SPEAKER_ONLY.match(line)
        if match:
            label = re.sub(r"\s+", "", match.group(1))
            stamp = match.group(2) or ""
            # 找下一行非空内容
            nxt = index + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            body = lines[nxt].strip() if nxt < len(lines) else ""
            if body and not _SPEAKER_ONLY.match(body) and not _looks_like_speaker_body(body):
                # 时间戳必须留在**行首**才会被 `_parse_timestamp` 吃掉。
                # 不能写成 `发言人1 00:10：正文`——那样 `00:10` 不在行首，
                # 整份文稿的时间轴会全部退化成"按字数估算"，等待时长也算不出来。
                merged.append(f"{stamp + ' ' if stamp else ''}{label}：{body}")
                index = nxt + 1
                continue

        merged.append(line)
        index += 1
    return merged


def _looks_like_speaker_body(line: str) -> bool:
    """下一行本身是不是又一条「说话人：内容」？（是的话就不该被当成正文吞掉）"""
    for pattern, _name in _SPEAKER_PATTERNS:
        if pattern.match(line):
            return True
    return False


# --------------------------------------------------------------------------- #
# 角色推断：给"只标了编号"的文稿判断谁是面试官
# --------------------------------------------------------------------------- #
#: 提问特征词：出现这些，这句多半是面试官在问
_INTERVIEWER_MARKERS = (
    "介绍一下",
    "说说",
    "说一下",
    "讲讲",
    "聊一聊",
    "谈一谈",
    "举个例子",
    "你觉得",
    "你认为",
    "你怎么",
    "你们公司",
    "有没有",
    "是不是",
    "能不能",
    "什么",
    "怎么",
    "为什么",
    "哪个",
    "哪些",
    "多少",
    "多久",
)
#: 提问句的疑问语气词（放句尾）
_INTERROGATIVE_TAILS = ("吗", "呢", "吧", "么")
#: 这些是"被问"的典型开头：候选人回答时常用
_ANSWER_MARKERS = ("好的", "对的", "是的", "不是", "我觉", "我叫", "我是", "可以")


def _question_score(segments: Sequence[SegmentDraft]) -> float:
    """给一组发言打"像面试官在提问"的分。分母是发言条数，便于不同说话量之间比较。"""
    if not segments:
        return 0.0
    score = 0.0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if text.endswith(("？", "?")):
            score += 2.0
        if any(marker in text for marker in _INTERVIEWER_MARKERS):
            score += 1.0
        if text.endswith(_INTERROGATIVE_TAILS):
            score += 1.0
        # 面试官也会说"可以/好的"这种收尾话，但那不是提问；候选人则大量用
        if text.startswith(_ANSWER_MARKERS) and not text.endswith(("？", "?")):
            score -= 0.5
    return score / len(segments)


def _answer_score(segments: Sequence[SegmentDraft]) -> float:
    """给一组发言打"像候选人在答题"的分＝平均发言长度。

    **为什么光看提问不够**：技术讨论式的面试里，面试官会用大量短促的确认
    （"是吧？""对不对？""这个角度。"）来推动候选人往下讲，候选人也会反问确认。
    这时"谁在提问"两边差不多，判定就该失灵——真实样本上正是这样。

    而无论什么面试，**讲方案的都是候选人**，所以发言长度是个更稳的信号：
    出题是几句，解题是几段。
    """
    texts = [segment.text.strip() for segment in segments if segment.text.strip()]
    if not texts:
        return 0.0
    return sum(len(text) for text in texts) / len(texts)


def infer_speaker_roles(drafts: Sequence[SegmentDraft]) -> str:
    """给没有角色语义的说话人（发言人1/2、Speaker A/B）判断谁是面试官。

    两路信号，**"谁在答题"优先于"谁在提问"**：

    1. **发言长度**——讲方案的是候选人。出题是几句，解题是几段，
       这个差别在真实转写里最明显，而且不受提问方式影响。
    2. **提问密度**——提问多的是面试官。作为补充与交叉验证。

    只看提问会在技术讨论式面试上失效（双方都在用反问确认），所以长度是主判据。
    一致时采信；**冲突时以长度为准**，因为它是更直接的结构性信号。

    就地改写 `drafts` 里没有角色语义的段落；已经标好
    `interviewer`/`candidate` 的文稿（`[面试官]` / `[我]`）**原样不动**——
    用户明确标了就不该被我们的猜测覆盖。

    返回推断依据（"" 表示没有改动）。**调用方应当把它展示给用户**：
    这是猜测，猜错时用户得知道去哪里改（用 `[面试官]` / `[我]` 显式标注）。
    """
    groups: dict[str, list[SegmentDraft]] = {}
    for draft in drafts:
        if draft.speaker in ("interviewer", "candidate"):
            # 已经有明确标记：这份文稿不需要推断
            return ""
        groups.setdefault(draft.speaker, []).append(draft)

    # 只有 unknown 一种说话人 → 没有任何可用于区分角色的信号
    if len(groups) < 2:
        return ""

    asked = {name: _question_score(items) for name, items in groups.items()}
    answered = {name: _answer_score(items) for name, items in groups.items()}

    # 主判据：平均发言最长的是候选人
    by_length = sorted(answered.items(), key=lambda item: item[1], reverse=True)
    candidate, candidate_len = by_length[0]
    runner_up, runner_up_len = by_length[1]

    # 两个人的发言长度都差不多（比如一句对一句的快速问答）→ 长度这个信号无效
    if candidate_len <= 0 or candidate_len < runner_up_len * 1.2:
        # 退回到提问密度，但仍然要求分差够大
        by_ask = sorted(asked.items(), key=lambda item: item[1], reverse=True)
        asker, asker_score = by_ask[0]
        _, second_score = by_ask[1]
        if asker_score <= 0 or asker_score - second_score < 0.35:
            return ""
        # 分差够大说明两个人都在问问题（比如候选人在反问环节问了半天），
        # 这时候硬猜会把角色搞反——宁可保持 unknown，让用户自己标。
        candidate = asker  # 提问少的那个是候选人
        for name in groups:
            if name != asker:
                candidate = name
                break
        basis = f"按「谁在提问」推断：{asker} 是面试官（提问密度 {asker_score:.1f}）"
    else:
        basis = (
            f"按「谁在讲方案」推断：{candidate} 是候选人"
            f"（平均每条 {candidate_len:.0f} 字，对方 {runner_up_len:.0f} 字）"
        )

    for draft in drafts:
        if draft.speaker == candidate:
            draft.speaker = "candidate"
        elif draft.speaker in groups:
            draft.speaker = "interviewer"

    return (
        f"{basis}，其余为面试官。"
        "如果反了，请在文稿里用 `[面试官]` / `[我]` 标出来。"
    )


def _fill_timestamps(drafts: list[SegmentDraft], *, chars_per_second: float) -> None:
    """补齐时间轴。有明确时间戳的段落用它对齐，没有的按字数推算。"""
    cursor = 0
    for index, draft in enumerate(drafts):
        next_start = drafts[index + 1].start_ms if index + 1 < len(drafts) else None
        duration = _estimate_ms(draft.text, chars_per_second=chars_per_second)

        if draft.start_ms is None:
            draft.start_ms = cursor

        end = draft.start_ms + duration
        # 下一段有明确时间戳时，本段结束点不能晚于它；但也**不能硬拉到它**——
        # 中间那段间隔是"等待"，不是发言时长（硬拉的话等待时长永远算成 0）
        if next_start is not None and next_start > draft.start_ms:
            end = min(end, next_start)
        draft.end_ms = end
        cursor = end


# --------------------------------------------------------------------------- #
# 第二层：段落 → 问题单元
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class QuestionUnit:
    idx: int
    question: str
    kind: str = "new_topic"  # opening | followup | new_topic | closing
    topic: str = ""
    question_type: str = "general"
    followups: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)

    ask_duration_ms: int = 0
    ask_end_ms: int | None = None
    answer_start_ms: int | None = None
    answer_ms: int | None = None
    wait_ms: int | None = None
    interrupted: bool = False

    @property
    def depth(self) -> int:
        return len(self.followups)


def _looks_like_question(text: str) -> bool:
    if "？" in text or "?" in text:
        return True
    return any(marker in text for marker in QUESTION_MARKERS)


def _split_into_questions(text: str) -> list[str]:
    """一段面试官发言里可能连着好几个问句，拆开。"""
    stripped = text.strip()
    if not stripped:
        return []

    # 先按问号切；没有问号但有疑问词就整段算一个问题
    pieces = [piece.strip() for piece in re.split(r"[？?]+", stripped) if piece.strip()]
    questions = [f"{piece}？" if not piece.endswith(("。", "！", "!")) else piece for piece in pieces]
    questions = [question for question in questions if _looks_like_question(question)]

    if not questions and _looks_like_question(stripped):
        questions = [stripped]
    return questions


def _is_closing(text: str) -> bool:
    return any(marker in text for marker in CLOSING_MARKERS)


def _is_followup(text: str, *, answered: bool) -> bool:
    """这段面试官发言是"追问"还是"新话题"？

    规则：候选人已经回答过上一题，且这段以追问词开头、或本身很短（面试官打断式追问）。
    """
    if not answered:
        return False
    stripped = text.strip()
    if len(stripped) <= 40 and any(stripped.startswith(marker) for marker in FOLLOWUP_MARKERS):
        return True
    return False


def split_questions(segments: Sequence[SegmentDraft]) -> list[QuestionUnit]:
    units: list[QuestionUnit] = []
    pending_index: int | None = None
    answered = False

    for segment in segments:
        if segment.speaker == "interviewer":
            questions = _split_into_questions(segment.text)
            if not questions:
                continue

            if pending_index is not None and answered and _is_followup(segment.text, answered=True):
                unit = units[pending_index]
                unit.followups.extend(questions)
                unit.ask_end_ms = segment.end_ms
                unit.ask_duration_ms += max(0, (segment.end_ms or 0) - (segment.start_ms or 0))
                answered = False
                continue

            # 面试官在候选人还在说的时候就开口 → 打断
            if pending_index is not None and not answered:
                if segment.start_ms is not None and units[pending_index].ask_end_ms is not None:
                    pass  # 同一次提问的续说，不算打断
                else:
                    units[pending_index].interrupted = True

            share = max(0, (segment.end_ms or 0) - (segment.start_ms or 0)) // max(1, len(questions))
            for position, question in enumerate(questions):
                kind = "closing" if _is_closing(question) else ("opening" if not units else "new_topic")
                units.append(
                    QuestionUnit(
                        idx=len(units),
                        question=question,
                        kind=kind,
                        ask_duration_ms=share if position == 0 else 0,
                        ask_end_ms=segment.end_ms,
                    )
                )
            pending_index = len(units) - 1
            answered = False

        elif segment.speaker == "candidate":
            if pending_index is None:
                continue
            unit = units[pending_index]
            duration = max(0, (segment.end_ms or 0) - (segment.start_ms or 0))
            unit.answer_ms = (unit.answer_ms or 0) + duration
            if unit.answer_start_ms is None and segment.start_ms is not None:
                unit.answer_start_ms = segment.start_ms
                if unit.ask_end_ms is not None and segment.start_ms > unit.ask_end_ms:
                    unit.wait_ms = segment.start_ms - unit.ask_end_ms
            answered = True

    return units


def apply_labels(units: Sequence[QuestionUnit], labels: dict[int, dict[str, Any]]) -> None:
    """把打标结果写回问题单元（就地修改）。

    单独抽出来是因为"打标"可能来自 LLM、也可能来自规则/人工——
    写回逻辑只应有一份。
    """
    for unit in units:
        label = labels.get(unit.idx)
        if not label:
            continue
        unit.question_type = str(label.get("type") or unit.question_type)
        unit.topic = str(label.get("topic") or unit.topic)
        unit.key_points = [str(item) for item in (label.get("key_points") or [])][:4]


# --------------------------------------------------------------------------- #
# 第三层：问题单元 → 量化特征
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PacingFeatures:
    question_count: int = 0
    interviewer_wpm: int | None = None
    candidate_wpm: int | None = None
    avg_question_len: float = 0.0
    followup_depth: float = 0.0
    wait_ms: int | None = None
    interrupt_rate: float = 0.0
    speaking_ratio: float | None = None
    topic_distribution: dict[str, float] = field(default_factory=dict)
    stress_level: str = "medium"
    opening_pattern: str = ""
    closing_pattern: str = ""
    duration_ms: int | None = None
    sources: dict[str, str] = field(default_factory=dict)


def _wpm(segments: Sequence[SegmentDraft], speaker: str) -> int | None:
    text_chars = sum(len(segment.text) for segment in segments if segment.speaker == speaker)
    total_ms = sum(
        max(0, (segment.end_ms or 0) - (segment.start_ms or 0))
        for segment in segments
        if segment.speaker == speaker
    )
    if text_chars == 0 or total_ms <= 0:
        return None
    return int(round(text_chars / (total_ms / 60000)))


def compute_features(
    units: Sequence[QuestionUnit],
    segments: Sequence[SegmentDraft],
    *,
    labels: dict[int, dict[str, Any]] | None = None,
) -> PacingFeatures:
    labels = labels or {}
    features = PacingFeatures(question_count=len(units))
    if not units:
        return features

    features.interviewer_wpm = _wpm(segments, "interviewer")
    features.candidate_wpm = _wpm(segments, "candidate")
    features.avg_question_len = round(sum(len(unit.question) for unit in units) / len(units), 1)
    features.followup_depth = round(sum(unit.depth for unit in units) / len(units), 2)

    waits = [unit.wait_ms for unit in units if unit.wait_ms]
    if waits:
        features.wait_ms = int(statistics.median(waits))
        features.sources["wait_ms"] = "measured"
    else:
        features.wait_ms = DEFAULT_WAIT_MS
        features.sources["wait_ms"] = "estimated"

    if features.interviewer_wpm:
        features.sources["interviewer_wpm"] = "measured"
    else:
        features.interviewer_wpm = 240
        features.sources["interviewer_wpm"] = "estimated"

    # 打断率：面试官发言开始时间早于前一段候选人发言结束时间 → 判定为插话
    candidate_segments = [segment for segment in segments if segment.speaker == "candidate"]
    if candidate_segments:
        interruptions = 0
        for index, segment in enumerate(segments):
            if segment.speaker != "interviewer" or segment.start_ms is None:
                continue
            for previous in reversed(segments[:index]):
                if previous.speaker == "candidate":
                    if previous.end_ms is not None and segment.start_ms < previous.end_ms:
                        interruptions += 1
                    break
        features.interrupt_rate = round(interruptions / len(candidate_segments), 3)
        features.sources["interrupt_rate"] = "measured"
    else:
        features.sources["interrupt_rate"] = "estimated"

    interviewer_ms = sum(
        max(0, (segment.end_ms or 0) - (segment.start_ms or 0))
        for segment in segments
        if segment.speaker == "interviewer"
    )
    candidate_ms = sum(
        max(0, (segment.end_ms or 0) - (segment.start_ms or 0))
        for segment in segments
        if segment.speaker == "candidate"
    )
    if interviewer_ms + candidate_ms > 0:
        features.speaking_ratio = round(interviewer_ms / (interviewer_ms + candidate_ms), 3)
        features.sources["speaking_ratio"] = "measured"

    # 题型分布：有打标结果就用它，否则回退到结构分类。
    # 追问**也要计入**——否则一个连追四层的面试官在分布里只显示"问了一题"，
    # 完全看不出他的风格（turn 级的追问正是压力来源）。
    counts: dict[str, int] = {}
    for unit in units:
        if labels:
            label = labels.get(unit.idx) or {}
            key = str(label.get("type") or unit.kind or "general")
        else:
            key = unit.kind
        counts[key] = counts.get(key, 0) + 1
        if unit.followups:
            counts["followup"] = counts.get("followup", 0) + len(unit.followups)

    total = sum(counts.values()) or 1
    features.topic_distribution = {
        key: round(value / total, 3) for key, value in sorted(counts.items(), key=lambda pair: -pair[1])
    }

    # 压力水平：追问深浅 + 是否频繁打断
    if features.followup_depth >= 1.5 or features.interrupt_rate >= 0.3:
        features.stress_level = "high"
    elif features.followup_depth >= 0.8 or features.interrupt_rate >= 0.15:
        features.stress_level = "medium"
    else:
        features.stress_level = "low"

    first_question = units[0].question
    features.opening_pattern = "要求自我介绍" if ("自我介绍" in first_question or "介绍一下自己" in first_question) else "直接进入项目/技术"
    features.closing_pattern = "有反问/薪资环节" if any(unit.kind == "closing" for unit in units) else "面试官直接收尾"

    features.duration_ms = max((segment.end_ms or 0) for segment in segments) if segments else None
    return features


# --------------------------------------------------------------------------- #
# 第四层：特征 → 画像 params
# --------------------------------------------------------------------------- #
def build_style_tags(features: PacingFeatures) -> list[str]:
    tags: list[str] = []
    if features.followup_depth >= 1.5:
        tags.append("紧盯细节连续追问")
    if features.interrupt_rate >= 0.3:
        tags.append("经常打断")
    elif features.interrupt_rate >= 0.15:
        tags.append("偶尔插话")
    if features.stress_level == "high":
        tags.append("压迫感强")
    if features.wait_ms is not None and features.wait_ms <= 800:
        tags.append("问完就等你答")
    elif features.wait_ms is not None and features.wait_ms >= 2500:
        tags.append("给足思考时间")
    if features.interviewer_wpm and features.interviewer_wpm >= 260:
        tags.append("语速偏快")
    if features.speaking_ratio is not None and features.speaking_ratio >= 0.6:
        tags.append("面试官主导节奏")
    if features.opening_pattern == "要求自我介绍":
        tags.append("固定要求自我介绍")
    if not tags:
        tags.append("节奏平缓")
    return tags


def build_params(features: PacingFeatures, units: Sequence[QuestionUnit]) -> dict[str, Any]:
    """生成 `interviewer_profile.params`（结构见 docs/PLAN.md §6.2）。"""
    max_depth = max(1, min(5, int(round(features.followup_depth)) + 1))

    triggers: list[str] = []
    if features.followup_depth >= 1:
        triggers.append("回答缺少量化数据")
    if features.stress_level in {"high", "medium"}:
        triggers.append("回答里出现含糊表述")
    triggers.append("没有讲到取舍或边界")
    if features.interrupt_rate >= 0.15:
        triggers.append("回答过长时打断")

    return {
        "version": 1,
        "source_features": {
            "question_count": features.question_count,
            "duration_ms": features.duration_ms,
            "followup_depth": features.followup_depth,
            "interrupt_rate": features.interrupt_rate,
            "speaking_ratio": features.speaking_ratio,
            "avg_question_len": features.avg_question_len,
        },
        "pace": {
            "interviewer_wpm": features.interviewer_wpm or 240,
            "candidate_wpm_target": features.candidate_wpm or 200,
            "wait_ms": features.wait_ms or DEFAULT_WAIT_MS,
        },
        "followup": {
            "max_depth": max_depth,
            "trigger": triggers,
        },
        "mix": features.topic_distribution,
        "stress": features.stress_level,
        "patterns": {
            "opening": features.opening_pattern,
            "closing": features.closing_pattern,
        },
        "style_tags": build_style_tags(features),
        "sources": features.sources,
        "sample_questions": [unit.question for unit in units[:8]],
    }


# --------------------------------------------------------------------------- #
# 第五层：params → 运行时策略（模拟时真正生效的东西）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PacingPolicy:
    silence_ms: int
    max_followup_depth: int
    followup_triggers: list[str]
    topic_mix: dict[str, float]
    style_tags: list[str]
    interviewer_wpm: int
    stress_level: str
    opening_pattern: str
    closing_pattern: str
    profile_id: int | None = None
    profile_name: str = ""
    #: 这个面试官**真实问过的原题**。
    #:
    #: 这是"让模拟更像那个面试官"最关键的一项：只给模型题型比例（"技术细节 50%"），
    #: 它只能泛泛地问；把原题给它，它才会问出这个人真正关心的点
    #: （比如他反复追问「你和机械臂那边怎么对接」）。
    sample_questions: list[str] = field(default_factory=list)
    #: 真实的题型配比（自我介绍 / 项目深挖 / 技术细节 / 反问…），**按题型**统计。
    #: 与 `topic_mix` 的区别很重要：`topic_mix` 是"问题结构"比例
    #: （新话题/追问/开场），对"问什么内容"几乎没有指导意义。
    type_mix: dict[str, float] = field(default_factory=dict)
    #: 真实开场原话（取自第一道题），比"直接进入项目/技术"这种概括有用得多
    opening_line: str = ""
    #: 真实收尾原话（取自有反问/薪资环节的那道题）
    closing_line: str = ""

    def effective_type_mix(self) -> dict[str, float]:
        """真正用来告诉模型"该问哪一类题"的配比。

        按**题型**统计（技术细节 / 项目深挖 / 反问…）。

        `topic_mix` 只在**没有真实题型时**兜底，且要用 `QUESTION_TYPE_KEYS`
        滤掉结构键。两个理由：

        1. 结构比例（新话题/追问/开场）与题型不是一个维度，混着显示会出现
           「new_topic 86% + 技术细节 62%」这种加起来超过 100%、互相矛盾的东西；
        2. 结构键没有中文标签（`TOPIC_LABELS` 里没有 `new_topic`），
           会以英文原样漏进 prompt。

        保留兜底是为了**旧画像不失效**：历史数据没有样本表，只剩 `params["mix"]`
        （那里面装的是真实题型，例如 `{"tech": 0.6}`）。
        """
        if self.type_mix:
            return {key: value for key, value in self.type_mix.items() if value > 0}
        return {
            key: value
            for key, value in self.topic_mix.items()
            if value > 0 and key in QUESTION_TYPE_KEYS
        }


def build_policy(
    params: dict[str, Any] | None,
    *,
    default_silence_ms: int = 900,
    profile_id: int | None = None,
    profile_name: str = "",
    samples: Sequence[Any] | None = None,
) -> PacingPolicy:
    """把画像 params 转成运行时策略。

    这是"画像在模拟中生效"的唯一入口：语音链路用它覆盖 VAD 静音阈值，
    编排链路用它生成面试官行为指令。

    `samples`：这段画像的节奏样本（`PacingSample`）。传进来是为了取**真实原题**——
    `params["sample_questions"]` 只存了前 8 道，而且可能是旧版数据没有这个字段；
    样本表里才有完整、可靠的原题。
    """
    # 不信任输入：params 可能来自模型输出或历史数据，字段类型都可能是错的
    data = params if isinstance(params, dict) else {}
    pace = data.get("pace") if isinstance(data.get("pace"), dict) else {}
    followup = data.get("followup") if isinstance(data.get("followup"), dict) else {}
    patterns = data.get("patterns") if isinstance(data.get("patterns"), dict) else {}

    raw_wait = pace.get("wait_ms")
    try:
        # 画像里没有等待时长时，用调用方给的默认沉默阈值（而不是另造一个魔法数）
        wait_ms = int(raw_wait) if raw_wait is not None else default_silence_ms
    except (TypeError, ValueError):
        wait_ms = default_silence_ms

    raw_mix = data.get("mix")
    mix: dict[str, float] = {}
    for key, value in (raw_mix if isinstance(raw_mix, dict) else {}).items():
        try:
            mix[str(key)] = round(float(value), 3)
        except (TypeError, ValueError):
            continue

    depth = followup.get("max_depth")
    try:
        max_depth = int(depth) if depth is not None else DEFAULT_FOLLOWUP_DEPTH
    except (TypeError, ValueError):
        max_depth = DEFAULT_FOLLOWUP_DEPTH

    wpm = pace.get("interviewer_wpm")
    try:
        interviewer_wpm = int(wpm) if wpm is not None else 240
    except (TypeError, ValueError):
        interviewer_wpm = 240

    # 真实原题：优先用样本表（完整、可靠），退回到 params 里存的那 8 道。
    questions = _pick_representative_questions(samples, data.get("sample_questions"))

    return PacingPolicy(
        # 面试官问完等得越久，我们的 VAD 也应该给用户更多沉默时间再判定"说完了"
        silence_ms=max(300, min(6000, wait_ms)),
        max_followup_depth=max(1, min(5, max_depth)),
        followup_triggers=[str(item) for item in (followup.get("trigger") or [])][:6],
        topic_mix=mix,
        style_tags=[str(item) for item in (data.get("style_tags") or [])][:8],
        interviewer_wpm=interviewer_wpm,
        stress_level=str(data.get("stress") or "medium"),
        opening_pattern=str(patterns.get("opening") or ""),
        closing_pattern=str(patterns.get("closing") or ""),
        profile_id=profile_id,
        profile_name=profile_name,
        sample_questions=questions,
        type_mix=_type_mix(samples),
        opening_line=_opening_line(samples, questions),
        closing_line=_closing_of(questions),
    )


def _type_mix(samples: Sequence[Any] | None) -> dict[str, float]:
    """按**题型**统计配比（自我介绍 / 项目深挖 / 技术细节 / 反问…）。

    与 params["mix"]（问题结构：新话题/追问/开场）不是一回事：
    告诉模型"86% 是新话题"几乎没有指导意义，而"一半的题都在问技术细节"很具体。
    """
    counts: dict[str, int] = {}
    for item in samples or []:
        key = str(getattr(item, "question_type", "") or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values()) or 1
    return {
        key: round(value / total, 3)
        for key, value in sorted(counts.items(), key=lambda pair: -pair[1])
    }


def _pick_representative_questions(    samples: Sequence[Any] | None,
    fallback: Sequence[Any] | None,
    *,
    limit: int = 16,
) -> list[str]:
    """从真实原题里挑**有代表性**的一批，而不是简单取前 N 条。

    为什么不能取前 N：转写稿的开头往往是寒暄与时间确认（「哪个21年？」「这是啊21年.？」），
    直接截前 24 条会让 prompt 里塞满噪音，真正有信息量的技术题一条都进不去。

    策略：**按题型分组轮转**（每类先取一条，再取第二条…），
    组内优先"像一道完整问题"的（长度适中、以问号结尾）。
    这样自我介绍、项目深挖、技术细节、反问都能被代表性覆盖。
    """
    buckets: dict[str, list[str]] = {}
    seen: set[str] = set()

    def add(key: str, raw: object) -> None:
        text = str(raw or "").strip()
        if len(text) < 6 or text in seen:
            return
        seen.add(text)
        buckets.setdefault(key or "general", []).append(text)

    for item in samples or []:
        add(str(getattr(item, "question_type", "") or "general"), getattr(item, "question", ""))
    for raw in fallback or []:
        # params 里的旧数据没有题型，统一放一桶
        add("general", raw)

    # 组内排序：更像"完整问题"的排前面（长度适中 + 有问号）
    def quality(text: str) -> tuple[int, int]:
        return (1 if text.endswith(("？", "?")) else 0, -abs(len(text) - 24))

    for items in buckets.values():
        items.sort(key=quality, reverse=True)

    picked: list[str] = []
    round_index = 0
    while len(picked) < limit:
        added = False
        for key in sorted(buckets, key=lambda k: -len(buckets[k])):
            items = buckets[key]
            if round_index < len(items):
                picked.append(items[round_index])
                added = True
                if len(picked) >= limit:
                    break
        if not added:
            break
        round_index += 1
    return picked


def _first_of(questions: Sequence[str], *, max_len: int = 60) -> str:
    """取第一道题当"开场原话"。太长的（转写里常有一大段连说）截断。"""
    for question in questions:
        if len(question) <= max_len:
            return question
    return ""


def _opening_line(samples: Sequence[Any] | None, questions: Sequence[str]) -> str:
    """真实开场：取**时间上最早**的那道题。

    不能从"代表性原题"里挑——那是按题型轮转出来的，可能取到面试中段的题，
    当开场话术就完全不对了（实测会把「不是mas系统跟max跟现在什么关系？」
    当成开场语）。所以这里按样本顺序找第一道像样的题。
    """
    for item in samples or []:
        text = str(getattr(item, "question", "") or "").strip()
        # 太短的（「是。」「吧。」）当不了开场词
        if 6 <= len(text) <= 60:
            return text
    return _first_of(questions)


def _closing_of(questions: Sequence[str]) -> str:
    """找出"收尾那类问题"的原话（反问 / 薪资 / 还有什么想问）。"""
    for question in questions:
        if any(marker in question for marker in CLOSING_MARKERS):
            return question[:80]
    return ""


def _is_presentable_question(text: str) -> bool:
    """这条能不能当"他问过的题"摆出来（展示 + 注入 prompt）。

    **与 `_looks_like_question` 是两件事，别合并**：

    - 提问判定统计的是"这个人提问的频率"，**不能**滤掉「是吧？」这类短句——
      那正是用户想学的话术，滤掉会让题型配比失真；
    - 而"他问过什么问题"要的是**题目**：陈述句（"方向点就是平面看去是多少度。
      0到360这个角度。"）和没有信息量的半句（"怎么算角度。"）摆出来只会
      干扰模型，让人以为这个面试官问得零碎。

    规则保持很松：只排除"句号结尾的陈述"和"短到没信息量的"。
    """
    stripped = text.strip()
    if len(stripped) < 6:
        return False
    if stripped.endswith(("。", "．", "！", "!")):
        return False
    return True


def policy_prompt_block(policy: PacingPolicy) -> str:
    """把画像转成给面试官模型的行为指令。

    这是"按学到的节奏面试"最直接的落地。三层信息，从粗到细：

    1. **节奏**：追问深度、压力水平、语速、等待时长；
    2. **题型配比**：这个面试官爱问哪一类（技术细节 / 项目深挖 / 反问…）；
    3. **真实原题**：他上次到底问了什么。只给比例时模型只能泛泛地问；
       把原题摆出来，它才会问出这个人真正关心的点。

    第 3 层是"像不像那个面试官"的关键——所以原题要**当作素材**给模型，
    同时明确要求它结合本次岗位/简历改编，而不是原样背出来（那会变成背题）。
    """
    parts = [f"【面试官画像：{policy.profile_name or '默认'}】"]

    # 「他问过什么题」这一路要过滤：这里要的是**题目**，不是口头语。
    # 注意与提问判定（_looks_like_question）分开——判定那边**不能**滤掉
    # 「是吧？」这类短句，那是用户想学的"话术"；而作为"他问过的题"摆出来，
    # 陈述句（句号结尾）和没有信息量的半句只会干扰模型。
    questions = [text for text in policy.sample_questions if _is_presentable_question(text)]
    if questions:
        # 放在最前面：这是最具体、最能决定"问什么"的信息
        lines = "\n".join(f"{index}. {text}" for index, text in enumerate(questions[:16], 1))
        parts.append(
            "■ 这个面试官在真实面试里问过下面这些问题（请**优先围绕这些题**提问，"
            "结合本次岗位与简历自然改编，不要原样照念、也不要提「你上次说过」）：\n" + lines
        )

    # 题型配比：合并 type_mix 与旧数据的 topic_mix（见 effective_type_mix）
    type_lines = [
        f"- {TOPIC_LABELS.get(key, key)}：{round(ratio * 100)}%"
        for key, ratio in sorted(policy.effective_type_mix().items(), key=lambda pair: -pair[1])
    ]
    if type_lines:
        parts.append("■ 他的题型配比（整场尽量贴近）：\n" + "\n".join(type_lines))

    if policy.style_tags:
        parts.append("风格：" + "、".join(policy.style_tags))
    # "他的"而不是"你的"：这一整段都是**面试官**的特征（这是给面试官模型看的
    # system 指令）。写成"你的语速"容易被读成候选人的语速，进而把语气带偏。
    if policy.interviewer_wpm:
        parts.append(
            f"压力水平：{policy.stress_level}；他的语速约 {policy.interviewer_wpm} 字/分钟（偏快就利落些）"
        )
    else:
        parts.append(f"压力水平：{policy.stress_level}")
    parts.append(
        f"最多追问 {policy.max_followup_depth} 层；遇到这些情况必须追问："
        + "、".join(policy.followup_triggers or ["回答缺少细节"])
    )
    if policy.opening_line:
        parts.append(f"开场用类似这样的话术：{policy.opening_line}")
    elif policy.opening_pattern:
        parts.append(f"开场方式：{policy.opening_pattern}")
    if policy.closing_line:
        parts.append(f"收尾用类似这样的话术：{policy.closing_line}")
    elif policy.closing_pattern:
        parts.append(f"收尾方式：{policy.closing_pattern}")
    return "\n".join(parts)


def policy_summary(policy: PacingPolicy) -> dict[str, Any]:
    """给前端展示"这个画像会怎么面你"（不暴露原始 params 的复杂度）。"""
    return {
        "profile_id": policy.profile_id,
        "profile_name": policy.profile_name,
        "silence_ms": policy.silence_ms,
        "max_followup_depth": policy.max_followup_depth,
        "followup_triggers": policy.followup_triggers,
        # 题型配比（这个面试官爱问哪一类）——这是"问什么"的依据
        # 用合并视图：新画像按题型、旧画像退回 params 里的 mix，两边都翻成中文标签
        "type_mix": {TOPIC_LABELS.get(key, key): ratio for key, ratio in policy.effective_type_mix().items()},
        # 问题结构配比（新话题/追问/开场），保留给旧前端与旧数据
        "topic_mix": {TOPIC_LABELS.get(key, key): ratio for key, ratio in policy.topic_mix.items()},
        "style_tags": policy.style_tags,
        "stress_level": policy.stress_level,
        "interviewer_wpm": policy.interviewer_wpm,
        "opening_pattern": policy.opening_pattern,
        "closing_pattern": policy.closing_pattern,
        # 真实原题：前端可以直接展示"他会问这些"，让"学到了什么"可见
        "sample_questions": policy.sample_questions,
        "opening_line": policy.opening_line,
        "closing_line": policy.closing_line,
    }
