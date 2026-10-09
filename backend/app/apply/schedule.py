"""投递排期的时间计算（纯函数）。

调度这类代码的 bug 几乎都出在**边界**上，而不是"能不能跑"：跨天、恰好等于设定时刻、
服务重启后错过了时间点。所以时间计算单独抽成纯函数，用测试把边界钉住，
真正起定时任务的那一层只剩"到点了就调用它"。

设计上的三个决定：

1. **用"每天 HH:MM"而不是 cron 表达式**。这个功能只需要"每天几点自动跑一次"，
   引入 cron 解析既增加出错面，也让界面难做（用户要写 `0 9 * * 1-5` 吗？）。
2. **允许一个补跑窗口**（`grace`）。服务不是常驻的（本地优先、用户关机就没了），
   所以"9:00 该跑但服务 9:20 才起来"必须能补跑，否则这个功能基本不会触发。
   但窗口不能无限大——下午才开机不该把早上的投递补上（那时岗位和 HR 状态都变了）。
3. **missed 与 upcoming 分开返回**。调用方要知道"这次是按时跑、还是补跑"，
   因为补跑时该在结果里说明，否则用户会奇怪"我今天没点它怎么投了"。

## 时间基准：**本地时间、naive datetime**

这个模块里所有 `datetime` 都是**本地时间的 naive 值**（没有 tzinfo）。理由：用户填的
"09:00" 指的是他手表上的九点——如果他填的是 UTC 九点，那这个功能就没法用了。

所以**不要**把 `clock.utcnow()`（aware UTC）直接传进这里：naive 与 aware 比较会直接抛
`TypeError`。调用方要先换成本地 naive：
`utcnow().astimezone().replace(tzinfo=None)`（`preview()` 已经这么做了）。
这个约束刻意写成显式的，因为踩过一次——混用 aware/naive 的报错信息
（`can't compare offset-naive and offset-aware datetimes`）完全看不出是时区问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

#: 允许补跑的时间窗（分钟）。超出就跳过这一次，等下一个时间点。
DEFAULT_GRACE_MINUTES = 30

#: 每天最多几个时间点（界面与校验都用它，避免用户填一长串把一天排满）
MAX_SLOTS_PER_DAY = 6


@dataclass(frozen=True, slots=True)
class DailySchedule:
    """每天在哪些时刻自动跑一次。`times` 为空表示没排期。"""

    times: tuple[time, ...] = ()
    enabled: bool = False

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.times)


def parse_hhmm(text: str) -> time | None:
    """把 `"09:30"` 解析成 `time`。认不出来返回 None（调用方据此拒绝，而不是猜）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def parse_schedule(raw: list[str] | None) -> tuple[time, ...]:
    """解析一组时间点：认不出的丢掉、去重、排序。

    **认不出的丢掉而不是报错**：这些值可能来自手改的配置。
    但一个都不认出来时调用方会看到空 tuple，从而不会自动跑——不会"猜一个时间"出来。
    """
    if not raw:
        return ()
    parsed = {moment for moment in (parse_hhmm(item) for item in raw) if moment is not None}
    ordered = sorted(parsed, key=lambda item: (item.hour, item.minute))
    return tuple(ordered[:MAX_SLOTS_PER_DAY])


def format_hhmm(moment: time) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def next_run_at(schedule: DailySchedule, *, now: datetime) -> datetime | None:
    """下一个应该跑的时刻（严格晚于 `now`）。没排期时返回 None。"""
    if not schedule.active:
        return None

    for moment in schedule.times:
        candidate = datetime.combine(now.date(), moment)
        if candidate > now:
            return candidate

    # 今天剩下的时间点都过了 → 明天的第一个
    first = schedule.times[0]
    return datetime.combine(now.date() + timedelta(days=1), first)


def due_run_at(
    schedule: DailySchedule,
    *,
    last_run_date: date | None,
    now: datetime,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
) -> datetime | None:
    """现在该不该跑？该跑就返回**它本该跑的那个时刻**，否则 None。

    判定：今天已经跑过 → 不跑；否则找一个"已经到点且在补跑窗口内"的时刻。
    多个时间点都到点时，取**最早的那个**（补跑只补一次，不连跑好几轮）。
    """
    if not schedule.active:
        return None
    if last_run_date is not None and last_run_date >= now.date():
        return None

    window = timedelta(minutes=max(0, grace_minutes))
    candidates = sorted(
        moment
        for moment in (
            datetime.combine(now.date(), slot)
            for slot in schedule.times
        )
        if moment <= now
    )
    if not candidates:
        return None

    # 逐个看是否还在自己的补跑窗口内，取**最早的那个仍然有效**的。
    #
    # 不能只看最早那个就放弃：9:00 与 9:30 两个时间点、现在 9:35 时，
    # 9:00 已经过期（35 分钟 > 30），但 9:30 正好该跑——只看最早那个会把这一次也丢掉，
    # 用户明明排了两个时间点却一个都没跑。
    for moment in candidates:
        if now - moment <= window:
            return moment
    return None


def describe_schedule(schedule: DailySchedule) -> str:
    """给界面/日志用的一句话。"""
    if not schedule.enabled:
        return "未启用定时投递"
    if not schedule.times:
        return "已启用，但没有填写时间点（不会自动跑）"
    return "每天 " + "、".join(format_hhmm(moment) for moment in schedule.times)
