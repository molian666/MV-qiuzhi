"""SM-2 间隔复习排程（纯函数）。

用标准 SM-2 而不是自己拍一套：它的行为被验证过几十年，公式简单到手算得出来，
而且"答得差就回到 1 天、答得好就按 ease 拉长"这个直觉与用户预期一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import utcnow

MIN_EASE = 1.3
DEFAULT_EASE = 2.5
FIRST_INTERVAL = 1.0
SECOND_INTERVAL = 6.0
MAX_INTERVAL = 365.0
# 低于这个 quality 视为"没答好"
PASS_QUALITY = 3


@dataclass(slots=True)
class Schedule:
    interval_days: float
    ease: float
    repetitions: int
    due_at: datetime
    passed: bool


def quality_from_score(score: float, *, low: float = 1.0, high: float = 5.0) -> int:
    """把 1–5 分映射到 SM-2 的 0–5 quality。

    用向上取整，好让 3 分刚好落在 SM-2 的及格线（quality >= 3）上——
    "5 分制下的 3 分算过关"符合直觉；若用四舍五入，3 分会落到 2 而被判为没答好。
    """
    if high <= low:
        return 0
    ratio = (float(score) - low) / (high - low)
    value = math.ceil(ratio * 5)
    return max(0, min(5, value))


def next_schedule(
    *,
    quality: int,
    interval_days: float = FIRST_INTERVAL,
    ease: float = DEFAULT_EASE,
    repetitions: int = 0,
    now: datetime | None = None,
) -> Schedule:
    """按 SM-2 算出下次复习时间。"""
    moment = now or utcnow()
    clamped_quality = max(0, min(5, int(quality)))

    if clamped_quality < PASS_QUALITY:
        # 没答好：回到 1 天重来，ease 略微下调（但不低于地板）
        new_ease = max(MIN_EASE, ease - 0.2)
        return Schedule(
            interval_days=FIRST_INTERVAL,
            ease=round(new_ease, 3),
            repetitions=0,
            due_at=moment + timedelta(days=FIRST_INTERVAL),
            passed=False,
        )

    if repetitions <= 0:
        interval = FIRST_INTERVAL
    elif repetitions == 1:
        interval = SECOND_INTERVAL
    else:
        interval = round(interval_days * ease, 2)

    interval = max(FIRST_INTERVAL, min(MAX_INTERVAL, interval))
    # 标准 SM-2 的 ease 更新公式
    delta = 0.1 - (5 - clamped_quality) * (0.08 + (5 - clamped_quality) * 0.02)
    new_ease = max(MIN_EASE, ease + delta)

    return Schedule(
        interval_days=interval,
        ease=round(new_ease, 3),
        repetitions=repetitions + 1,
        due_at=moment + timedelta(days=interval),
        passed=True,
    )


def urgency(due_at: datetime | None, *, now: datetime | None = None, horizon_days: float = 7.0) -> float:
    """到期紧迫度（0–1，越大越该优先）：越过期越接近 1，越远越小。"""
    if due_at is None:
        return 0.0
    moment = now or utcnow()
    days = (due_at - moment).total_seconds() / 86400
    if days <= 0:
        return 1.0
    if days >= horizon_days:
        return 0.0
    return max(0.0, min(1.0, 1.0 - days / horizon_days))
