"""表达体检：填充词、语速、停顿、音量突变、紧张度（纯函数）。

**分层说明**：纯函数只吃"转写回合 + 音量包络"，不读文件——
音量包络由 service 层从 WAV 算好喂进来，所以造一条音量序列就能验证停顿检测，
不需要准备真实音频。这条规矩是前面几个里程碑一以贯之的。

**每个问题都带 `at_ms`**（会话内绝对毫秒）：前端把整场录音跳到那句，
这样"你这里语速突然变快"是可核对的，而不是一句空评价。

**关于紧张度**：它是从语速波动、停顿、填充词密度、音量波动推出来的启发式指标，
**不是心理测量**。所以文案一律写成"可能的紧张信号"，不写成"你很紧张"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

# --------------------------------------------------------------------------- #
# 阈值（都是一眼能看懂的经验值，改了会影响所有报告，所以集中放在这里）
# --------------------------------------------------------------------------- #
#: 中文口语的字/分钟参考区间（面试回答的舒适区）
PACE_MIN_CPM = 150.0
PACE_MAX_CPM = 320.0
#: 语速窗口长度：10 秒够看出"这一段突然快了"，又不会碎到看不出趋势
WINDOW_MS = 10_000
#: 低于这个 RMS（0-1）算静音；16k pcm16 下 0.02 约等于环境底噪
SILENCE_RMS = 0.02
#: 停顿分级
PAUSE_MIN_MS = 600
PAUSE_LONG_MS = 1500
PAUSE_VERY_LONG_MS = 3000

#: 强填充词：一听就是"卡壳"，也是录回听时最容易数出来的
STRONG_FILLERS: tuple[str, ...] = (
    "嗯",
    "呃",
    "啊",
    "那个",
    "就是说",
    "怎么说呢",
    "对不对",
    "是吧",
    "你知道吗",
)
#: 弱填充词：日常口语里很常见，用多了显得啰嗦，但不至于"卡壳"
WEAK_FILLERS: tuple[str, ...] = (
    "然后",
    "就是",
    "其实",
    "反正",
    "所以说",
    "我觉得吧",
    "然后呢",
    "接下来呢",
    "那个时候",
)

#: 加权密度：强填充词的权重高，因为"嗯"和"然后"对听感的伤害不是一个量级
FILLER_WEIGHT: dict[str, float] = {word: 1.0 for word in STRONG_FILLERS}
FILLER_WEIGHT.update({word: 0.35 for word in WEAK_FILLERS})

#: 每千字加权填充词数超过这个值就值得改
FILLER_RATE_WARN = 8.0
FILLER_RATE_BAD = 16.0

#: 样本量下限。低于它"每千字密度"会失去意义——
#: 33 个字里 4 个填充词会算成每千字 121 个，看着像灾难，其实只是样本太小。
#: 所以样本不足时：密度照实计算并展示，但**不参与评分**。
MIN_CHARS_FOR_RATE = 200
#: 少于 20 秒的发言也不足以判断节奏
MIN_SPEAKING_MS = 20_000

#: 总发言时长低于这个值就不算语速。文字面试的回合没有音频时间戳，
#: 拿几毫秒当时长除出来的"字/分钟"是假数据（实测出现过 149620 字/分钟）。
MIN_SPEAKING_MS_FOR_PACE = 5_000

#: 单个回合短于这个时长就不算"一次发言"。真实的口头回答不会只有几十毫秒；
#: 这种量级只可能来自"文字模式回合没有时间戳"的占位值。
MIN_TURN_MS = 500


@dataclass(slots=True)
class TurnSpan:
    """一段发言（来自 session_turn，只保留体检需要的字段）。"""

    index: int
    speaker: str
    text: str
    start_ms: int = 0
    end_ms: int = 0
    topic: str = ""

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def chars(self) -> int:
        return len([char for char in self.text if not char.isspace()])


@dataclass(slots=True)
class FillerHit:
    word: str
    count: int
    weight: float
    strong: bool

    @property
    def weighted(self) -> float:
        return round(self.count * self.weight, 2)


@dataclass(slots=True)
class WindowMetric:
    start_ms: int
    end_ms: int
    chars: float = 0.0
    cpm: float = 0.0
    rms: float = 0.0


@dataclass(slots=True)
class PauseSpan:
    start_ms: int
    duration_ms: int
    kind: str = "normal"  # normal | long | very_long
    turn_index: int | None = None
    before_text: str = ""
    after_text: str = ""

    @property
    def seconds(self) -> float:
        return round(self.duration_ms / 1000, 2)


@dataclass(slots=True)
class TurnIssue:
    """定位到具体一处的问题（前端据此跳播）。"""

    kind: str
    severity: str  # warning | info
    at_ms: int
    excerpt: str = ""
    detail: str = ""
    turn_index: int | None = None


@dataclass(slots=True)
class ExpressionReport:
    speaking_ms: int = 0
    chars: int = 0
    overall_cpm: float = 0.0
    filler_total: int = 0
    filler_weighted: float = 0.0
    filler_per_kchar: float = 0.0
    fillers: list[FillerHit] = field(default_factory=list)
    pauses: list[PauseSpan] = field(default_factory=list)
    longest_pause: PauseSpan | None = None
    windows: list[WindowMetric] = field(default_factory=list)
    pace_cv: float = 0.0
    volume_cv: float = 0.0
    tension: float = 0.0
    score: float = 0.0
    issues: list[TurnIssue] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    summary: str = ""
    has_audio: bool = False
    #: 样本够不够（字数与时长）。不够时密度类指标只展示、不参与评分
    reliable: bool = True


# --------------------------------------------------------------------------- #
# 填充词
# --------------------------------------------------------------------------- #
def count_fillers(text: str, *, include_weak: bool = True) -> list[FillerHit]:
    """数填充词。按出现次数降序，强填充词优先。"""
    content = text or ""
    if not content:
        return []

    words = list(STRONG_FILLERS) + (list(WEAK_FILLERS) if include_weak else [])
    hits: list[FillerHit] = []
    for word in words:
        count = content.count(word)
        if count <= 0:
            continue
        hits.append(
            FillerHit(
                word=word,
                count=count,
                weight=FILLER_WEIGHT.get(word, 0.35),
                strong=word in STRONG_FILLERS,
            )
        )

    return sorted(hits, key=lambda item: (-item.weighted, item.word))


def filler_density(weighted_total: float, chars: int) -> float:
    """每千字加权填充词数（千字比"绝对值"更能跨长度比较）。"""
    if chars <= 0:
        return 0.0
    return round(weighted_total / (chars / 1000), 2)


# --------------------------------------------------------------------------- #
# 语速
# --------------------------------------------------------------------------- #
def speaker_turns(turns: Sequence[TurnSpan], speaker: str = "candidate") -> list[TurnSpan]:
    """所有发言回合。**不过滤时长**——文字模式的回合没有音频时间戳，
    但里面的填充词、字数照样能分析，没有理由整场丢掉。"""
    return [turn for turn in turns if turn.speaker == speaker]


def timed_turns(turns: Sequence[TurnSpan], speaker: str = "candidate") -> list[TurnSpan]:
    """有时长、能用来做节奏分析的回合（见 MIN_TURN_MS 的说明）。"""
    return [turn for turn in speaker_turns(turns, speaker) if turn.duration_ms >= MIN_TURN_MS]


def overall_pace(turns: Sequence[TurnSpan], speaker: str = "candidate") -> tuple[int, int, float]:
    """(总时长 ms, 总字数, 字/分钟)。

    - 字数取**全部**发言（文字模式也算得出来）；
    - 时长与语速只取有时长的那些回合；时长不够时语速返回 0，
      而不是拿几毫秒硬除出一个假数字（实测出现过 149620 字/分钟）。
    """
    speaking_ms = sum(turn.duration_ms for turn in timed_turns(turns, speaker))
    chars = sum(turn.chars for turn in speaker_turns(turns, speaker))
    if speaking_ms < MIN_SPEAKING_MS_FOR_PACE:
        return speaking_ms, chars, 0.0
    return speaking_ms, chars, round(chars / (speaking_ms / 60_000), 1)


def pace_windows(
    turns: Sequence[TurnSpan],
    *,
    speaker: str = "candidate",
    window_ms: int = WINDOW_MS,
) -> list[WindowMetric]:
    """按固定窗口聚合语速。

    一个 30 秒的回答会横跨 3 个窗口——**字数按与窗口的重叠时长比例分摊**，
    否则语速曲线会随"回答恰好被窗口切开"而跳变。
    """
    rows = timed_turns(turns, speaker)
    if not rows or window_ms <= 0:
        return []

    start = min(turn.start_ms for turn in rows)
    end = max(turn.end_ms for turn in rows)
    windows: list[WindowMetric] = []

    cursor = start
    while cursor < end:
        window_end = min(cursor + window_ms, end)
        span_ms = window_end - cursor
        chars = 0.0

        for turn in rows:
            overlap = min(turn.end_ms, window_end) - max(turn.start_ms, cursor)
            if overlap <= 0:
                continue
            chars += turn.chars * (overlap / turn.duration_ms) if turn.duration_ms else 0.0

        metric = WindowMetric(start_ms=cursor, end_ms=window_end, chars=round(chars, 2))
        metric.cpm = round(chars / (span_ms / 60_000), 1) if span_ms > 0 else 0.0
        windows.append(metric)
        cursor = window_end

    return windows


def pace_variability(windows: Sequence[WindowMetric]) -> float:
    """语速变异系数（标准差/均值）。越大说明忽快忽慢。"""
    values = [window.cpm for window in windows if window.chars > 0]
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return round(math.sqrt(variance) / mean, 3)


# --------------------------------------------------------------------------- #
# 停顿与音量
# --------------------------------------------------------------------------- #
def _frame_at(ms: int, frame_ms: int) -> int:
    return max(0, ms // max(1, frame_ms))


def detect_pauses(
    turns: Sequence[TurnSpan],
    volume: Sequence[float] | None,
    *,
    speaker: str = "candidate",
    frame_ms: int = 50,
    silence_rms: float = SILENCE_RMS,
    min_ms: int = PAUSE_MIN_MS,
) -> list[PauseSpan]:
    """在候选人发言内部找停顿（连续静音帧 ≥ min_ms）。

    只在自己的回合里找：回合之间的空隙是**面试官在说话**，不是你在卡壳。
    没有音量数据时返回空列表（文字模式没有音频，这是正常的，不是错误）。
    """
    rows = timed_turns(turns, speaker)
    if not rows or not volume:
        return []

    pauses: list[PauseSpan] = []
    for turn in rows:
        first = _frame_at(turn.start_ms, frame_ms)
        last = min(_frame_at(turn.end_ms, frame_ms), len(volume))

        run_start: int | None = None
        for frame in range(first, last):
            level = volume[frame] if frame < len(volume) else 0.0
            if level < silence_rms:
                if run_start is None:
                    run_start = frame
                continue

            if run_start is not None:
                duration = (frame - run_start) * frame_ms
                if duration >= min_ms:
                    pauses.append(_make_pause(pauses, turn, run_start, duration, frame_ms))
                run_start = None

        if run_start is not None:
            duration = (last - run_start) * frame_ms
            if duration >= min_ms:
                pauses.append(_make_pause(pauses, turn, run_start, duration, frame_ms))

    return pauses


def _make_pause(
    existing: Sequence[PauseSpan],
    turn: TurnSpan,
    run_start: int,
    duration: int,
    frame_ms: int,
) -> PauseSpan:
    # run_start 是**绝对帧号**（从整场音频起点算），所以时间点直接乘帧长；
    # 这里曾经写成 turn.start_ms + run_start * frame_ms，导致定位到整场末尾（测试抓到了）
    at_ms = run_start * frame_ms
    if duration >= PAUSE_VERY_LONG_MS:
        kind = "very_long"
    elif duration >= PAUSE_LONG_MS:
        kind = "long"
    else:
        kind = "normal"

    # 用停顿在回合内的相对位置，粗略切出"停顿前后说了什么"（供回听时定位）
    ratio = (at_ms - turn.start_ms) / turn.duration_ms if turn.duration_ms else 0.0
    cut = int(len(turn.text) * min(max(ratio, 0.0), 1.0))
    return PauseSpan(
        start_ms=at_ms,
        duration_ms=duration,
        kind=kind,
        turn_index=turn.index,
        before_text=turn.text[max(0, cut - 20) : cut].strip(),
        after_text=turn.text[cut : cut + 20].strip(),
    )


def volume_envelope_stats(
    turns: Sequence[TurnSpan],
    volume: Sequence[float],
    *,
    speaker: str = "candidate",
    frame_ms: int = 50,
) -> tuple[float, float, list[tuple[int, float, float]]]:
    """返回 (整体音量变异系数, 平均 RMS, [(turn_index, 前半段均值, 后半段均值)])。"""
    rows = timed_turns(turns, speaker)
    if not rows or not volume:
        return 0.0, 0.0, []

    levels: list[float] = []
    halves: list[tuple[int, float, float]] = []

    for turn in rows:
        first = _frame_at(turn.start_ms, frame_ms)
        last = min(_frame_at(turn.end_ms, frame_ms), len(volume))
        segment = list(volume[first:last])
        if not segment:
            continue
        levels.extend(segment)

        middle = max(1, len(segment) // 2)
        front = sum(segment[:middle]) / middle
        back_values = segment[middle:] or segment
        back = sum(back_values) / len(back_values)
        halves.append((turn.index, round(front, 4), round(back, 4)))

    if not levels:
        return 0.0, 0.0, halves

    mean = sum(levels) / len(levels)
    if mean <= 0:
        return 0.0, 0.0, halves
    variance = sum((level - mean) ** 2 for level in levels) / len(levels)
    return round(math.sqrt(variance) / mean, 3), round(mean, 4), halves


# --------------------------------------------------------------------------- #
# 问题定位
# --------------------------------------------------------------------------- #
def _turn_at(turns: Sequence[TurnSpan], ms: int) -> TurnSpan | None:
    for turn in turns:
        if turn.start_ms <= ms < turn.end_ms:
            return turn
    return None


def detect_issues(
    turns: Sequence[TurnSpan],
    windows: Sequence[WindowMetric],
    pauses: Sequence[PauseSpan],
    halves: Sequence[tuple[int, float, float]],
    *,
    speaker: str = "candidate",
) -> list[TurnIssue]:
    """把各类信号变成"带时间位置的具体问题"。"""
    issues: list[TurnIssue] = []

    # 语速过快/过慢：只报最极端的几个窗口，否则一次面试能报出几十条
    fast = sorted(
        [window for window in windows if window.chars >= 8 and window.cpm > PACE_MAX_CPM],
        key=lambda window: -window.cpm,
    )[:3]
    slow = sorted(
        [window for window in windows if window.chars >= 8 and window.cpm < PACE_MIN_CPM],
        key=lambda window: window.cpm,
    )[:2]

    for window in fast:
        turn = _turn_at(turns, window.start_ms)
        issues.append(
            TurnIssue(
                kind="pace_fast",
                severity="warning" if window.cpm > PACE_MAX_CPM * 1.25 else "info",
                at_ms=window.start_ms,
                excerpt=(turn.text[:40] if turn else ""),
                turn_index=turn.index if turn else None,
                detail=f"这一段语速约 {round(window.cpm)} 字/分钟（舒适区 {int(PACE_MIN_CPM)}–{int(PACE_MAX_CPM)}）",
            )
        )

    for window in slow:
        turn = _turn_at(turns, window.start_ms)
        issues.append(
            TurnIssue(
                kind="pace_slow",
                severity="warning" if window.cpm < PACE_MIN_CPM * 0.75 else "info",
                at_ms=window.start_ms,
                excerpt=(turn.text[:40] if turn else ""),
                turn_index=turn.index if turn else None,
                detail=f"这一段语速约 {round(window.cpm)} 字/分钟，偏慢且容易显得不确定",
            )
        )

    for pause in pauses:
        if pause.kind == "normal":
            continue
        issues.append(
            TurnIssue(
                kind="long_pause",
                severity="warning" if pause.kind == "very_long" else "info",
                at_ms=pause.start_ms,
                excerpt=(pause.before_text + " … " + pause.after_text).strip(),
                turn_index=pause.turn_index,
                detail=f"这里停了 {pause.seconds} 秒",
            )
        )

    # 音量骤降：同一段回答后半段比前半段低 35% 以上（"越说越小声"）
    for turn_index, front, back in halves:
        turn = next((item for item in turns if item.index == turn_index), None)
        if turn is None or front <= 0:
            continue
        ratio = back / front
        if ratio <= 0.65:
            issues.append(
                TurnIssue(
                    kind="volume_drop",
                    severity="info",
                    at_ms=turn.start_ms + turn.duration_ms // 2,
                    excerpt=turn.text[-40:],
                    turn_index=turn_index,
                    detail=f"这半句音量比前一段低了 {round((1 - ratio) * 100)}%（常见于越说越没底气）",
                )
            )
        elif ratio >= 1.6:
            issues.append(
                TurnIssue(
                    kind="volume_spike",
                    severity="info",
                    at_ms=turn.start_ms + turn.duration_ms // 2,
                    excerpt=turn.text[-40:],
                    turn_index=turn_index,
                    detail=f"这半句音量比前一段高了 {round((ratio - 1) * 100)}%",
                )
            )

    return sorted(issues, key=lambda item: item.at_ms)


# --------------------------------------------------------------------------- #
# 紧张度与评分
# --------------------------------------------------------------------------- #
def tension_score(
    *,
    pace_cv: float,
    volume_cv: float,
    pause_count: int,
    very_long_pauses: int,
    filler_per_kchar: float,
    speaking_minutes: float,
) -> float:
    """0–100 的"紧张信号强度"（**启发式推断，不是心理测量**）。

    四项各自归一后加权：
    - 语速忽快忽慢（变异系数）；
    - 长停顿频率（按每分钟折算，长面试不因为时间长而虚高）；
    - 填充词密度；
    - 音量波动。
    """
    minutes = max(0.5, speaking_minutes)

    pace_part = min(1.0, pace_cv / 0.5) * 30
    pause_part = min(1.0, (pause_count / minutes) / 6) * 20
    very_long_part = min(1.0, (very_long_pauses / minutes) / 2) * 20
    filler_part = min(1.0, filler_per_kchar / FILLER_RATE_BAD) * 20
    volume_part = min(1.0, volume_cv / 0.8) * 10

    return round(min(100.0, pace_part + pause_part + very_long_part + filler_part + volume_part), 1)


def expression_score(
    *,
    overall_cpm: float,
    pace_cv: float,
    filler_per_kchar: float,
    very_long_pauses: int,
    speaking_minutes: float,
    has_audio: bool,
) -> float:
    """0–10 的表达分（与 M3 的 rubric 同尺度，方便并进 dimension_scores）。"""
    if overall_cpm <= 0:
        return 0.0

    # 语速：落在舒适区给满分，越偏扣越多
    if PACE_MIN_CPM <= overall_cpm <= PACE_MAX_CPM:
        pace_score = 10.0
    elif overall_cpm < PACE_MIN_CPM:
        pace_score = max(3.0, 10 - (PACE_MIN_CPM - overall_cpm) / 20)
    else:
        pace_score = max(3.0, 10 - (overall_cpm - PACE_MAX_CPM) / 25)

    # 填充词：8/千字扣 2 分，16/千字扣 4 分
    filler_penalty = min(4.0, filler_per_kchar / 4)

    # 语速波动
    stability_penalty = min(2.0, pace_cv * 4)

    # 很长停顿（每分钟 1 次以上开始扣）
    if has_audio:
        pause_penalty = min(2.0, (very_long_pauses / max(0.5, speaking_minutes)) * 1.5)
    else:
        pause_penalty = 0.0

    score = pace_score - filler_penalty - stability_penalty - pause_penalty
    return round(max(0.0, min(10.0, score)), 1)


def build_suggestions(report: ExpressionReport) -> list[str]:
    """针对这次的具体问题给练法（每条都要今天就能做）。"""
    tips: list[str] = []

    strong_fillers = [item for item in report.fillers if item.strong]
    if report.reliable and report.filler_per_kchar >= FILLER_RATE_WARN and strong_fillers:
        top = "、".join(f"「{item.word}」{item.count} 次" for item in strong_fillers[:3])
        tips.append(
            f"填充词偏多（{top}）。练法：把整场录音过一遍，**每听到一次就记一笔**；"
            "然后练替换动作——想说「嗯」时先闭嘴停半秒，把停顿当成标点用，而不是用声音填满它。"
        )

    fast = [item for item in report.issues if item.kind == "pace_fast"]
    if fast:
        tips.append(
            "有几段语速明显偏快（最容易出在『这个我熟』的地方）。"
            "练法：把其中一段的稿子念三遍——第一遍正常、第二遍放慢 1.3 倍、"
            "第三遍回到正常，让嘴记住「慢」的感觉；每个句号处留半秒。"
        )

    slow = [item for item in report.issues if item.kind == "pace_slow"]
    if slow:
        tips.append(
            "有几段偏慢且拖沓，通常不是嘴慢而是**边想边说**。"
            "练法：回答前先在纸上写 5 个关键词（不写整句），按关键词说。"
        )

    long_pauses = [item for item in report.issues if item.kind == "long_pause"]
    if long_pauses:
        worst = max(long_pauses, key=lambda item: item.at_ms)
        tips.append(
            f"有 {len(long_pauses)} 处停顿超过 1.5 秒（最长一处在你说的「{worst.excerpt or '某段'}」中间）。"
            "练法：准备 3 句万能过渡——「我从另一个角度说」「我先说结论，再说原因」"
            "「这块我分两部分讲」——想不起来时用它把话接住。"
        )

    volume = [item for item in report.issues if item.kind in {"volume_drop", "volume_spike"}]
    if volume:
        tips.append(
            "有几句音量前后落差较大。练法：录一段 60 秒回答，回听时只注意**最后三句**——"
            "结尾句刻意提高一点音量，听起来会更有结论感。"
        )

    if report.pace_cv and report.pace_cv > 0.35 and not fast and not slow:
        tips.append("整体语速起伏偏大。练法：同一段内容连续说三遍，尽量让三遍的用时接近（±10 秒内）。")

    if not report.has_audio:
        tips.append(
            "这场没有录音，所以只做了文字层面的分析（填充词与整体语速）。"
            "用「语音面试」跑一场，才能看到停顿、音量与逐段语速曲线。"
        )

    if not tips:
        tips.append("这场表达没有明显问题。保持节奏，重点可以放在内容深度上。")

    return tips


def summarize(report: ExpressionReport) -> str:
    if report.chars == 0:
        return "这次没有可分析的回答内容。"

    head = f"共说 {report.chars} 字、{round(report.speaking_ms / 1000)} 秒，整体语速 {round(report.overall_cpm)} 字/分钟"

    if not report.reliable:
        # 样本不足时先说清楚，避免"每千字 100 个填充词"把人吓到
        head = f"（说得太少，以下仅供参考）{head}；这次有 {report.filler_total} 处填充词"
        return head

    if report.filler_per_kchar >= FILLER_RATE_BAD:
        head += f"；填充词偏多（每千字 {report.filler_per_kchar} 个，主要是「{report.fillers[0].word}」）"
    elif report.filler_per_kchar >= FILLER_RATE_WARN:
        head += f"；填充词略多（每千字 {report.filler_per_kchar} 个）"

    if report.longest_pause is not None and report.longest_pause.kind != "normal":
        head += f"；最长停顿 {report.longest_pause.seconds} 秒"

    if report.has_audio and report.tension >= 55:
        head += "；语速与音量波动较大，可能是紧张信号"
    elif report.pace_cv > 0.35:
        head += "；语速起伏偏大"

    return head


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def analyze(
    turns: Sequence[TurnSpan],
    *,
    volume: Sequence[float] | None = None,
    frame_ms: int = 50,
    speaker: str = "candidate",
) -> ExpressionReport:
    """做一次表达体检。

    `volume` 是逐帧 RMS（0–1）；没有音频时传 None，报告会降级为文字层面的分析。
    """
    report = ExpressionReport()
    report.has_audio = bool(volume)

    rows = speaker_turns(turns, speaker)
    if not rows:
        report.summary = "这次没有你的发言内容，没什么可分析的。"
        return report

    text = "".join(turn.text for turn in rows)
    speaking_ms, chars, overall_cpm = overall_pace(turns, speaker)
    report.speaking_ms = speaking_ms
    report.chars = chars
    report.overall_cpm = overall_cpm

    fillers = count_fillers(text)
    report.fillers = fillers
    report.filler_total = sum(item.count for item in fillers)
    report.filler_weighted = round(sum(item.weighted for item in fillers), 2)
    report.filler_per_kchar = filler_density(report.filler_weighted, chars)
    report.reliable = chars >= MIN_CHARS_FOR_RATE and speaking_ms >= MIN_SPEAKING_MS

    windows = pace_windows(turns, speaker=speaker)
    report.pace_cv = pace_variability(windows)

    pauses = detect_pauses(turns, volume, speaker=speaker, frame_ms=frame_ms)
    report.pauses = pauses
    report.longest_pause = max(pauses, key=lambda item: item.duration_ms, default=None)

    volume_cv = 0.0
    halves: list[tuple[int, float, float]] = []
    if volume:
        volume_cv, _mean, halves = volume_envelope_stats(turns, volume, speaker=speaker, frame_ms=frame_ms)
    report.volume_cv = volume_cv

    # 把窗口的音量填上（供前端画"语速-音量"双曲线）
    if volume:
        for window in windows:
            first = _frame_at(window.start_ms, frame_ms)
            last = min(_frame_at(window.end_ms, frame_ms), len(volume))
            segment = list(volume[first:last])
            if segment:
                window.rms = round(sum(segment) / len(segment), 4)
    report.windows = windows

    very_long = sum(1 for pause in pauses if pause.kind == "very_long")
    minutes = speaking_ms / 60_000

    # 样本不足时，最受样本量影响的这两项（填充密度、长停顿次数）不参与评分
    filler_for_score = report.filler_per_kchar if report.reliable else 0.0
    very_long_for_score = very_long if report.reliable else 0

    report.tension = tension_score(
        pace_cv=report.pace_cv,
        volume_cv=volume_cv,
        pause_count=sum(1 for pause in pauses if pause.kind != "normal"),
        very_long_pauses=very_long_for_score,
        filler_per_kchar=filler_for_score,
        speaking_minutes=minutes,
    )
    report.score = expression_score(
        overall_cpm=overall_cpm,
        pace_cv=report.pace_cv,
        filler_per_kchar=filler_for_score,
        very_long_pauses=very_long_for_score,
        speaking_minutes=minutes,
        has_audio=report.has_audio,
    )

    report.issues = detect_issues(turns, windows, pauses, halves, speaker=speaker)
    report.suggestions = build_suggestions(report)
    report.summary = summarize(report)
    return report
