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
    "design": "系统设计",
    "scenario": "场景题",
    "salary": "薪资与期望",
    "reverse": "反问环节",
    "general": "通用问题",
    "followup": "追问",
}

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

_SPEAKER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*[\[\(【]?(面试官|面试官A|面试官B|hr|HR|Hr|q|Q)[\]\)】]?\s*[:：]?\s*(.+)$"), "interviewer"),
    (re.compile(r"^\s*[\[\(【]?(我|候选人|本人|求职者|a|A|答)[\]\)】]?\s*[:：]?\s*(.+)$"), "candidate"),
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


def parse_transcript(text: str, *, chars_per_second: float = CHARS_PER_SECOND) -> list[SegmentDraft]:
    """解析对话文稿。支持这些写法（可以混用）：

        [面试官] 先做个自我介绍吧
        00:12 面试官：你简历里这个项目讲一下
        我：我做的是订单系统
        Q: 为什么离职
        A: 因为想换方向
    """
    drafts: list[SegmentDraft] = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        start_ms, line = _parse_timestamp(line)
        if not line:
            continue

        speaker = "unknown"
        content = line
        for pattern, name in _SPEAKER_PATTERNS:
            match = pattern.match(line)
            if match:
                speaker = name
                content = match.group(2).strip()
                break

        if not content:
            continue

        drafts.append(SegmentDraft(idx=len(drafts), speaker=speaker, text=content, start_ms=start_ms))

    _fill_timestamps(drafts, chars_per_second=chars_per_second)
    return drafts


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


def build_policy(
    params: dict[str, Any] | None,
    *,
    default_silence_ms: int = 900,
    profile_id: int | None = None,
    profile_name: str = "",
) -> PacingPolicy:
    """把画像 params 转成运行时策略。

    这是"画像在模拟中生效"的唯一入口：语音链路用它覆盖 VAD 静音阈值，
    编排链路用它生成面试官行为指令。
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
    )


def policy_prompt_block(policy: PacingPolicy) -> str:
    """把画像转成给面试官模型的行为指令。

    这是"按学到的节奏面试"最直接的落地：题型配比、追问深度、压力水平都写进 prompt，
    所以同一套题库在不同画像下会问出不同的面试。
    """
    mix_lines = [
        f"- {TOPIC_LABELS.get(key, key)}：{round(ratio * 100)}%"
        for key, ratio in sorted(policy.topic_mix.items(), key=lambda pair: -pair[1])
        if ratio > 0
    ]
    parts = [f"【面试官画像：{policy.profile_name or '默认'}】"]
    if policy.style_tags:
        parts.append("风格：" + "、".join(policy.style_tags))
    parts.append(f"压力水平：{policy.stress_level}；你的语速约 {policy.interviewer_wpm} 字/分钟（说话简短些）")
    parts.append(f"最多追问 {policy.max_followup_depth} 层；遇到这些情况必须追问：" + "、".join(policy.followup_triggers or ["回答缺少细节"]))
    if mix_lines:
        parts.append("整场题型配比（尽量贴近）：\n" + "\n".join(mix_lines))
    if policy.opening_pattern:
        parts.append(f"开场方式：{policy.opening_pattern}")
    if policy.closing_pattern:
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
        "topic_mix": {TOPIC_LABELS.get(key, key): ratio for key, ratio in policy.topic_mix.items()},
        "style_tags": policy.style_tags,
        "stress_level": policy.stress_level,
        "interviewer_wpm": policy.interviewer_wpm,
        "opening_pattern": policy.opening_pattern,
        "closing_pattern": policy.closing_pattern,
    }
