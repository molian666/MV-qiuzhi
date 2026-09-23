"""投递限速：宁可少投，也别把账号投废。

默认值直接写死在代码里（PLAN §6.7）：每分钟 ≤3、每天 ≤30、每两条之间 3–9 秒随机抖动。
这些数字远低于 Boss 每日 150 次招呼的上限——**主动留出安全边际**，
因为封号的代价（求职中断）远大于"多投几十个岗位"的收益。

纯函数 + 外部传入时钟：所以"一分钟内第 4 次会被拦"这种边界能直接用测试钉住。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

#: 允许用户在设置里调整的上限（不能超过它，避免一键把自己投废）
MAX_PER_MINUTE = 10
MAX_PER_DAY = 60


@dataclass(slots=True)
class ThrottleConfig:
    per_minute: int = 3
    per_day: int = 30
    jitter_min_seconds: float = 3.0
    jitter_max_seconds: float = 9.0

    def clamped(self) -> ThrottleConfig:
        """把配置夹到安全范围内（UI 上的输入不能突破天花板）。

        抖动下限允许为 0（"我自己手点，不想等"是合理需求），
        但**默认值仍是 3–9 秒**——固定间隔本身就是机器特征。
        """
        per_minute = max(1, min(MAX_PER_MINUTE, int(self.per_minute)))
        per_day = max(per_minute, min(MAX_PER_DAY, int(self.per_day)))
        low = max(0.0, float(self.jitter_min_seconds))
        high = max(low, float(self.jitter_max_seconds))
        return ThrottleConfig(per_minute=per_minute, per_day=per_day, jitter_min_seconds=low, jitter_max_seconds=high)


@dataclass(slots=True)
class ThrottleState:
    """限速状态（由 platform_account.daily_sent + 进程内最近发送时刻组成）。"""

    sent_today: int = 0
    recent_sends: list[datetime] = field(default_factory=list)

    def prune(self, *, now: datetime) -> None:
        cutoff = now - timedelta(minutes=1)
        self.recent_sends = [moment for moment in self.recent_sends if moment > cutoff]


@dataclass(slots=True)
class ThrottleDecision:
    allowed: bool
    reason: str = ""
    wait_seconds: float = 0.0


def can_send(state: ThrottleState, config: ThrottleConfig, *, now: datetime) -> ThrottleDecision:
    """现在能不能再投一条？不能的话要等多久。"""
    settings = config.clamped()

    if state.sent_today >= settings.per_day:
        return ThrottleDecision(
            allowed=False,
            reason=f"今日已达上限 {settings.per_day} 条（明天再继续，或去设置里确认后再调整）",
        )

    state.prune(now=now)
    if len(state.recent_sends) >= settings.per_minute:
        oldest = min(state.recent_sends)
        wait = 60 - (now - oldest).total_seconds()
        return ThrottleDecision(
            allowed=False,
            reason=f"本分钟已投 {settings.per_minute} 条，需等待 {max(1, int(wait))} 秒",
            wait_seconds=max(1.0, wait),
        )

    return ThrottleDecision(allowed=True)


def next_delay_seconds(config: ThrottleConfig, *, rng: random.Random | None = None) -> float:
    """两条投递之间的随机等待（人类化节奏，避免机械间隔被识别）。"""
    settings = config.clamped()
    source = rng or random.Random()
    return round(source.uniform(settings.jitter_min_seconds, settings.jitter_max_seconds), 2)


def record_send(state: ThrottleState, *, now: datetime) -> None:
    state.sent_today += 1
    state.recent_sends.append(now)
    state.prune(now=now)


def remaining_quota(state: ThrottleState, config: ThrottleConfig) -> int:
    settings = config.clamped()
    return max(0, settings.per_day - state.sent_today)
