"""沉默看门狗：候选人长时间不说话时该做什么（纯函数）。

**为什么需要它**：真实面试里沉默一定会被推动——面试官会问"还在吗"、"需要再想想吗"，
或者直接说"那我们换个问题"。而这个项目里没有这个机制，后果是**面试会无限期卡住**：
候选人放弃了、网断了、或者只是去接了个电话，会话就一直挂在那里，什么都不会发生。

**为什么必须是纯函数**：判定"该催了 / 该过了"的时刻很容易写错（尤其是"没说过话"和
"说过话后又沉默"这两种情况的阈值不一样），而且这类 bug 只在真实时间流逝时才暴露。
抽成纯函数之后，用几个数字就能把每条边界钉住。

三类状态分清楚：

1. **还没开口**（`has_spoken=False`）：大多是真的没准备好，先给一次鼓励性的台阶，
   再超时过题。
2. **说过话后沉默**（`has_spoken=True`）：可能是在想，也可能以为该面试官说话了。
   先问一句"还需要补充吗"，这既是真实面试官会做的，也给候选人一个台阶。
3. **催过一次还在沉默**：不再反复催（连着问三遍"在吗"会让人烦躁），直接过题。

`action` 用字符串而不是枚举，是为了能直接序列化进事件里给前端用。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 什么都不做
ACTION_NONE = "none"
#: 提醒一次（"还在吗"）
ACTION_NUDGE = "nudge"
#: 超时，过到下一题
ACTION_ADVANCE = "advance"

#: 每个状态下最多提醒几次（提醒完还沉默就过题，不反复催）
MAX_NUDGES = 1


@dataclass(frozen=True, slots=True)
class SilencePolicy:
    """沉默阈值。

    默认值按"真实面试的耐心"取：真人不会 5 秒没人应就跳过你，但也不会等 5 分钟。
    """

    #: 沉默多久开始提醒
    nudge_ms: int = 25_000
    #: 沉默多久算超时（过到下一题）
    timeout_ms: int = 45_000
    #: 还没开口时更宽容一点：候选人可能在找简历、调设备
    cold_start_nudge_ms: int = 35_000
    cold_start_timeout_ms: int = 70_000
    #: 最多提醒几次（超过就过题）
    max_nudges: int = MAX_NUDGES

    def clamped(self) -> "SilencePolicy":
        """把阈值夹到合理范围。

        提醒必须**早于**超时——否则提醒永远没机会出现（这个顺序反了是很容易犯的错，
        而且表现是"功能静默失效"）。所以这里强制保证 `nudge < timeout`。
        """
        nudge = max(1_000, int(self.nudge_ms))
        timeout = max(nudge + 1_000, int(self.timeout_ms))
        cold_nudge = max(1_000, int(self.cold_start_nudge_ms))
        cold_timeout = max(cold_nudge + 1_000, int(self.cold_start_timeout_ms))
        return SilencePolicy(
            nudge_ms=nudge,
            timeout_ms=timeout,
            cold_start_nudge_ms=cold_nudge,
            cold_start_timeout_ms=cold_timeout,
            max_nudges=max(0, int(self.max_nudges)),
        )


@dataclass(frozen=True, slots=True)
class SilenceDecision:
    """这一刻该做什么。`action` 为 `none` 时其余字段无意义。"""

    action: str
    elapsed_ms: int = 0
    message: str = ""

    @property
    def should_act(self) -> bool:
        return self.action != ACTION_NONE


#: 还没开口时的提醒。**给台阶，不给压力**——这一档最常见的原因是设备或没准备好。
COLD_NUDGE_MESSAGE = "还在吗？不着急，准备好了直接说就行。"
#: 说过话后沉默的提醒。真实面试官这时会问"还有要补充的吗"，而不是催。
WARM_NUDGE_MESSAGE = "还需要再补充一点吗？没有的话我们就继续下一题。"
#: 超时过题的说明
TIMEOUT_MESSAGE = "看起来这边先告一段落，我们继续下一个问题。"


def decide_silence(
    *,
    silence_ms: int,
    has_spoken: bool,
    nudges_sent: int = 0,
    policy: SilencePolicy | None = None,
) -> SilenceDecision:
    """按沉默时长决定：不动作 / 提醒一次 / 过题。

    `has_spoken` 表示**这一轮候选人有没有开过口**（不是整场）。它决定用哪套阈值。
    """
    config = (policy or SilencePolicy()).clamped()
    elapsed = max(0, int(silence_ms))
    sent = max(0, int(nudges_sent))

    nudge_at = config.nudge_ms if has_spoken else config.cold_start_nudge_ms
    timeout_at = config.timeout_ms if has_spoken else config.cold_start_timeout_ms

    # 超时优先于提醒：已经等太久了就别再补一句"还在吗"，直接过题
    if elapsed >= timeout_at:
        return SilenceDecision(action=ACTION_ADVANCE, elapsed_ms=elapsed, message=TIMEOUT_MESSAGE)

    if elapsed >= nudge_at and sent < config.max_nudges:
        message = WARM_NUDGE_MESSAGE if has_spoken else COLD_NUDGE_MESSAGE
        return SilenceDecision(action=ACTION_NUDGE, elapsed_ms=elapsed, message=message)

    return SilenceDecision(action=ACTION_NONE, elapsed_ms=elapsed)
