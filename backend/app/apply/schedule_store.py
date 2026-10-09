"""定时投递：排期配置的存取与"这次该做什么"的判定。

**职责边界**：时间计算全在 `schedule.py`（纯函数），这里只负责"把配置存下来、
把上次跑过的日期记下来"。真正起循环的那一层（`scheduler.py`）只做"到点了调用它"。

配置存在 `AppSetting` 的 JSON 里（与 `apply_settings` 一致），**不改表结构**。

关于"到底做什么"，只有两种，且**都不越过能力闸门**：

- `queue`：只抓岗位、过滤、打分、入待确认队列——**不投递**。这是 T0 能力
  （`scheduled_queue`），默认可以开。
- `apply`：抓完直接按自动投递的策略投出去。这是 T2 能力（`scheduled_apply`），
  要先开启自动投递并输入确认短语。这一层**只记录意图**，闸门由调用方校验——
  这样"没开自动投递就绝不会自动投"这句话只有一个地方需要成立。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, select

from ..clock import utcnow
from ..models.system import AppSetting
from .schedule import (
    DEFAULT_GRACE_MINUTES,
    DailySchedule,
    describe_schedule,
    due_run_at,
    next_run_at,
    parse_schedule,
)

SETTINGS_KEY = "apply_schedule"

MODE_QUEUE = "queue"
MODE_APPLY = "apply"

MODE_LABELS: dict[str, str] = {
    MODE_QUEUE: "只抓岗位入队（不投递）",
    MODE_APPLY: "抓完并自动投递",
}

#: 抓取用的关键词等（与手动「抓取候选」同一套参数）
DEFAULT_LIMIT = 10


@dataclass(slots=True)
class ScheduleSettings:
    enabled: bool = False
    #: "HH:MM" 文本形式（存库与界面都用它，解析交给 schedule.parse_schedule）
    times: list[str] = field(default_factory=list)
    mode: str = MODE_QUEUE
    #: 每次抓多少条
    limit: int = DEFAULT_LIMIT
    keywords: list[str] = field(default_factory=list)
    city: str = ""
    fetch_detail: bool = False
    grace_minutes: int = DEFAULT_GRACE_MINUTES
    #: 上次真正跑过的日期（用于"一天只跑一次"）
    last_run_date: date | None = None
    last_run_at: datetime | None = None
    last_result: str = ""

    @property
    def schedule(self) -> DailySchedule:
        return DailySchedule(times=parse_schedule(self.times), enabled=self.enabled)

    @property
    def parsed_times(self) -> tuple[Any, ...]:
        return parse_schedule(self.times)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_int(value: Any, fallback: int) -> int:
    """容错取整。

    这些值可能来自手改的配置文件（`"limit": "abc"`），或者被别的版本写坏。
    读配置时**不该因为一个坏字段就把整个页面打成 500**——退回默认值即可。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def load_schedule(db: Session) -> ScheduleSettings:
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}

    mode = str(data.get("mode") or MODE_QUEUE)
    raw_times = data.get("times")
    return ScheduleSettings(
        enabled=bool(data.get("enabled", False)),
        times=[str(item) for item in raw_times] if isinstance(raw_times, list) else [],
        mode=mode if mode in MODE_LABELS else MODE_QUEUE,
        limit=max(1, _as_int(data.get("limit"), DEFAULT_LIMIT)),
        keywords=[str(item) for item in (data.get("keywords") or []) if str(item).strip()],
        city=str(data.get("city") or ""),
        fetch_detail=bool(data.get("fetch_detail", False)),
        grace_minutes=_as_int(data.get("grace_minutes"), DEFAULT_GRACE_MINUTES),
        last_run_date=_as_date(data.get("last_run_date")),
        last_run_at=_as_datetime(data.get("last_run_at")),
        last_result=str(data.get("last_result") or ""),
    )


def save_schedule(db: Session, settings: ScheduleSettings) -> ScheduleSettings:
    payload = {
        "enabled": settings.enabled,
        # 存之前先解析一遍：认不出的丢在入口，别让它进库以后每次都要重新过滤
        "times": [f"{item.hour:02d}:{item.minute:02d}" for item in parse_schedule(settings.times)],
        "mode": settings.mode if settings.mode in MODE_LABELS else MODE_QUEUE,
        "limit": max(1, int(settings.limit)),
        "keywords": [str(item).strip() for item in settings.keywords if str(item).strip()],
        "city": settings.city.strip(),
        "fetch_detail": settings.fetch_detail,
        "grace_minutes": max(0, int(settings.grace_minutes)),
        "last_run_date": settings.last_run_date.isoformat() if settings.last_run_date else None,
        "last_run_at": settings.last_run_at.isoformat() if settings.last_run_at else None,
        "last_result": settings.last_result,
    }
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=payload))
    else:
        row.value = payload
        row.updated_at = utcnow()
        db.add(row)
    db.commit()
    return load_schedule(db)


def local_now() -> datetime:
    """本地时间的 naive 值。

    `clock.utcnow()` 是 aware UTC，而排期是**本地墙上时间**（用户填的 09:00 是他手表上的九点）。
    两者直接比较会抛 `can't compare offset-naive and offset-aware datetimes`——
    这个报错完全看不出是时区问题，所以在入口处一次性转换，不把这个约束漏到下面。
    """
    return utcnow().astimezone().replace(tzinfo=None)


def mark_run(db: Session, *, at: datetime | None = None, result: str = "") -> ScheduleSettings:
    """记下"这次跑过了"。

    `last_run_date` 与 `last_run_at` 都记：前者用于"一天只跑一次"的判定（它是**日期**，
    不受时区/夏令时影响），后者是给人看的。
    """
    settings = load_schedule(db)
    moment = at or utcnow()
    settings.last_run_date = moment.date()
    settings.last_run_at = moment
    settings.last_result = result[:500]
    return save_schedule(db, settings)


@dataclass(slots=True)
class SchedulePreview:
    """给界面看的"接下来会发生什么"。"""

    active: bool
    description: str
    next_run_at: datetime | None = None
    due_now: bool = False
    due_at: datetime | None = None
    last_run_at: datetime | None = None
    last_result: str = ""


def preview(db: Session, *, now: datetime | None = None) -> SchedulePreview:
    settings = load_schedule(db)
    # 排期按本地墙上时间判定（见 local_now 的说明）
    moment = now or local_now()
    schedule = settings.schedule
    due = due_run_at(
        schedule,
        last_run_date=settings.last_run_date,
        now=moment,
        grace_minutes=settings.grace_minutes,
    )
    return SchedulePreview(
        active=schedule.active,
        description=describe_schedule(schedule),
        next_run_at=next_run_at(schedule, now=moment),
        due_now=due is not None,
        due_at=due,
        last_run_at=settings.last_run_at,
        last_result=settings.last_result,
    )
