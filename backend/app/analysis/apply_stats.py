"""投递漏斗与转化统计（纯函数）。

不碰数据库：这类统计最容易算错，而且算错了不容易被发现（数字看着都"挺合理"）。
所以输入是一批快照对象，输出是前端直接可渲染的结构，对错全靠单测钉住。

一个关键细节：**被拒/无响应的投递也要算进"它曾到过的阶段"**。
只看当前状态的话，一个面到终面才被拒的投递会被算成"从未进入面试"，
漏斗会整体偏低——所以快照里带 `max_stage`（由事件流算出的历史最高阶段）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import median

#: 状态 → 漏斗主阶段
STAGE_OF_STATUS: dict[str, str] = {
    "applied": "applied",
    "viewed": "viewed",
    "screening": "viewed",  # "筛选中"归到"已查看"这一档
    "interview1": "interview",
    "interview2": "interview",
    "hr": "interview",
    "offer": "offer",
}

#: 漏斗阶段（顺序即漏斗顺序）
STAGES: tuple[tuple[str, str], ...] = (
    ("applied", "已投递"),
    ("viewed", "已查看"),
    ("interview", "面试"),
    ("offer", "Offer"),
)

_STAGE_ORDER = [key for key, _ in STAGES]

#: 视为"已有回应"的状态（用于回复率）
RESPONDED_STATUSES = frozenset(
    {"viewed", "screening", "interview1", "interview2", "hr", "offer", "rejected"}
)

#: 默认的"多久没动静就算需要跟进"
FOLLOW_UP_AFTER_DAYS = 7


@dataclass(slots=True)
class ApplicationSnapshot:
    """统计只需要这几个字段（不依赖 ORM，便于单测）。"""

    id: int
    company: str
    title: str
    channel: str
    status: str
    applied_at: datetime
    resume_version: str = ""
    #: 历史到过的最高阶段（由 application_event 算出；没有事件流时用当前状态推）
    max_stage: str = ""
    first_response_at: datetime | None = None

    @property
    def effective_stage(self) -> str:
        return self.max_stage or stage_of(self.status)

    @property
    def responded(self) -> bool:
        return self.status in RESPONDED_STATUSES or self.effective_stage != "applied"


@dataclass(slots=True)
class FunnelStep:
    stage: str
    label: str
    count: int
    rate_from_previous: float
    rate_from_first: float


@dataclass(slots=True)
class ChannelStats:
    channel: str
    total: int
    responded: int
    interviewed: int
    offers: int
    response_rate: float
    interview_rate: float
    offer_rate: float


@dataclass(slots=True)
class WeekPoint:
    week: str  # 例如 2026-W11
    applied: int
    responded: int
    interviewed: int


@dataclass(slots=True)
class ResumeVersionStats:
    version: str
    total: int
    responded: int
    response_rate: float


@dataclass(slots=True)
class ResponseTimeStats:
    samples: int
    median_hours: float | None = None
    average_hours: float | None = None
    fastest_hours: float | None = None
    slowest_hours: float | None = None


@dataclass(slots=True)
class FollowUpItem:
    application_id: int
    company: str
    title: str
    channel: str
    status: str
    days_since: float


def stage_of(status: str) -> str:
    return STAGE_OF_STATUS.get(status, "applied")


#: 状态进度序（数值越大越靠后）。终止态排在正常流程之后——
#: 它们同样是"最终结果"，但不表示进展，所以导入时另有覆盖规则（见 apply_service）。
STATUS_RANK: dict[str, int] = {
    "applied": 0,
    "viewed": 1,
    "screening": 2,
    "interview1": 3,
    "interview2": 4,
    "hr": 5,
    "offer": 6,
    "rejected": 7,
    "ghosted": 8,
    "withdrawn": 9,
}


def status_rank(status: str) -> int:
    return STATUS_RANK.get(status, 0)


def reached_stages(status: str, max_stage: str = "") -> list[str]:
    """这个投递到过哪些阶段（漏斗计数靠它）。"""
    effective = max_stage or stage_of(status)
    try:
        index = _STAGE_ORDER.index(effective)
    except ValueError:
        index = 0
    return _STAGE_ORDER[: index + 1]


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def build_funnel(apps: list[ApplicationSnapshot]) -> list[FunnelStep]:
    total = len(apps)
    counts = {stage: 0 for stage in _STAGE_ORDER}
    for app in apps:
        for stage in reached_stages(app.status, app.effective_stage):
            counts[stage] += 1

    steps: list[FunnelStep] = []
    previous = total
    for stage, label in STAGES:
        count = counts[stage]
        steps.append(
            FunnelStep(
                stage=stage,
                label=label,
                count=count,
                rate_from_previous=_rate(count, previous),
                rate_from_first=_rate(count, total),
            )
        )
        previous = count if count > 0 else previous
    return steps


def by_channel(apps: list[ApplicationSnapshot]) -> list[ChannelStats]:
    buckets: dict[str, list[ApplicationSnapshot]] = {}
    for app in apps:
        buckets.setdefault(app.channel or "other", []).append(app)

    stats: list[ChannelStats] = []
    for channel, items in buckets.items():
        total = len(items)
        responded = sum(1 for item in items if item.responded)
        interviewed = sum(1 for item in items if item.effective_stage in {"interview", "offer"})
        offers = sum(1 for item in items if item.effective_stage == "offer")
        stats.append(
            ChannelStats(
                channel=channel,
                total=total,
                responded=responded,
                interviewed=interviewed,
                offers=offers,
                response_rate=_rate(responded, total),
                interview_rate=_rate(interviewed, total),
                offer_rate=_rate(offers, total),
            )
        )

    stats.sort(key=lambda item: (-item.total, item.channel))
    return stats


def week_key(moment: datetime) -> str:
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def weekly_trend(apps: list[ApplicationSnapshot], *, weeks: int = 8, now: datetime | None = None) -> list[WeekPoint]:
    """最近 N 周的投递趋势（按投递时间所在周分组，补全空白周）。"""
    if now is not None:
        moment = now
    else:
        from ..clock import utcnow

        moment = utcnow()

    buckets: dict[str, WeekPoint] = {}
    for offset in range(weeks - 1, -1, -1):
        week_start = moment - timedelta(days=moment.weekday(), weeks=offset)
        key = week_key(week_start)
        buckets[key] = WeekPoint(week=key, applied=0, responded=0, interviewed=0)

    for app in apps:
        key = week_key(app.applied_at)
        point = buckets.get(key)
        if point is None:
            continue  # 超出窗口
        point.applied += 1
        if app.responded:
            point.responded += 1
        if app.effective_stage in {"interview", "offer"}:
            point.interviewed += 1

    return [buckets[key] for key in sorted(buckets)]


def resume_version_ab(apps: list[ApplicationSnapshot]) -> list[ResumeVersionStats]:
    """简历版本 A/B：哪个版本回复率更高。"""
    buckets: dict[str, list[ApplicationSnapshot]] = {}
    for app in apps:
        key = (app.resume_version or "").strip() or "（未标注）"
        buckets.setdefault(key, []).append(app)

    stats = [
        ResumeVersionStats(
            version=version,
            total=len(items),
            responded=sum(1 for item in items if item.responded),
            response_rate=_rate(sum(1 for item in items if item.responded), len(items)),
        )
        for version, items in buckets.items()
    ]
    stats.sort(key=lambda item: (-item.response_rate, -item.total, item.version))
    return stats


def response_time(apps: list[ApplicationSnapshot]) -> ResponseTimeStats:
    """投递到第一次有回应之间的耗时分布。"""
    hours: list[float] = []
    for app in apps:
        if app.first_response_at is None:
            continue
        delta = (app.first_response_at - app.applied_at).total_seconds() / 3600
        if delta >= 0:
            hours.append(delta)

    if not hours:
        return ResponseTimeStats(samples=0)

    return ResponseTimeStats(
        samples=len(hours),
        median_hours=round(median(hours), 1),
        average_hours=round(sum(hours) / len(hours), 1),
        fastest_hours=round(min(hours), 1),
        slowest_hours=round(max(hours), 1),
    )


def follow_ups(
    apps: list[ApplicationSnapshot],
    *,
    now: datetime | None = None,
    after_days: int = FOLLOW_UP_AFTER_DAYS,
) -> list[FollowUpItem]:
    """该跟进却还没动静的投递：投出去超过 N 天仍是"已投递/已查看"。"""
    from ..clock import utcnow

    moment = now or utcnow()
    items: list[FollowUpItem] = []
    for app in apps:
        if app.status not in {"applied", "viewed", "screening"}:
            continue
        days = (moment - app.applied_at).total_seconds() / 86400
        if days >= after_days:
            items.append(
                FollowUpItem(
                    application_id=app.id,
                    company=app.company,
                    title=app.title,
                    channel=app.channel,
                    status=app.status,
                    days_since=round(days, 1),
                )
            )

    items.sort(key=lambda item: -item.days_since)
    return items


def overview(apps: list[ApplicationSnapshot], *, weeks: int = 8, now: datetime | None = None) -> dict:
    """一次性给出前端看板需要的所有统计。"""
    funnel = build_funnel(apps)
    channels = by_channel(apps)
    responded = sum(1 for app in apps if app.responded)
    interviewed = sum(1 for app in apps if app.effective_stage in {"interview", "offer"})
    offers = sum(1 for app in apps if app.effective_stage == "offer")

    return {
        "total": len(apps),
        "responded": responded,
        "interviewed": interviewed,
        "offers": offers,
        "response_rate": _rate(responded, len(apps)),
        "interview_rate": _rate(interviewed, len(apps)),
        "offer_rate": _rate(offers, len(apps)),
        # 注意用 asdict：这些是 slots dataclass，没有 __dict__
        "funnel": [asdict(step) for step in funnel],
        "by_channel": [asdict(item) for item in channels],
        "weekly": [asdict(point) for point in weekly_trend(apps, weeks=weeks, now=now)],
        "resume_versions": [asdict(item) for item in resume_version_ab(apps)],
        "response_time": asdict(response_time(apps)),
        "follow_ups": [asdict(item) for item in follow_ups(apps, now=now)],
    }
