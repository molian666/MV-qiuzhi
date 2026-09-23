"""人脉跟进（纯函数）：算清楚谁该联系了、以及该说什么。

**为什么要把"该跟进"变成可算的东西**：靠脑子记「我是不是该问候一下老王了」一定会漏，
而且漏掉的往往是关键的那个（内推人）。所以每个联系人有一个**跟进节奏**（天），
再加上他自己承诺过的日期，取最早的那个作为"下次该联系的时间"。

另外，`suggest_message` 给的是**可以直接发出去的开场**，而不是「记得跟进」这种废话——
后者知道了也没用，前者点一下就能用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

#: 关系类型 → 中文标签
RELATIONS: dict[str, str] = {
    "referral": "内推人",
    "alumni": "校友",
    "hr": "HR",
    "headhunter": "猎头",
    "ex_colleague": "前同事",
    "interviewer": "面试官",
    "other": "其他",
}

#: 不同关系的默认跟进节奏（天）。内推最紧——内推完不及时问进展，事情就凉了。
DEFAULT_FOLLOW_UP_DAYS: dict[str, int] = {
    "referral": 5,
    "interviewer": 14,
    "hr": 7,
    "headhunter": 10,
    "alumni": 30,
    "ex_colleague": 45,
    "other": 14,
}

#: 未来这么久之内的都算"该准备联系了"
DEFAULT_HORIZON_DAYS = 14
#: 多少天以内算"马上"
SOON_DAYS = 3

DIRECTIONS: dict[str, str] = {"outbound": "我联系他", "inbound": "他联系我"}

INTERACTION_KINDS: dict[str, str] = {
    "message": "消息",
    "call": "通话",
    "meeting": "见面",
    "referral": "内推",
    "interview": "面试",
    "thanks": "致谢",
    "gift": "问候/礼物",
}

#: 固定日期的节日（春节/中秋等每年变动的节日不做，免得算错日期反而尴尬）
FIXED_GREETINGS: tuple[tuple[int, int, str, str], ...] = (
    (1, 1, "元旦", "元旦快乐，新的一年一切顺利！"),
    (5, 1, "劳动节", "劳动节快乐，好好休息几天。"),
    (10, 1, "国庆节", "国庆快乐！这几天有什么安排？"),
)


@dataclass(slots=True)
class InteractionSnapshot:
    at: datetime
    direction: str = "outbound"
    kind: str = "message"
    summary: str = ""
    #: 他答应过什么 / 你答应过什么
    promised: str = ""
    #: 明确约定的下次联系时间（有就用它，比默认节奏准）
    next_follow_up_at: datetime | None = None


@dataclass(slots=True)
class ContactSnapshot:
    id: int
    name: str
    relation: str = "other"
    company: str = ""
    title: str = ""
    channel: str = ""
    note: str = ""
    #: 0 表示用该关系的默认节奏
    follow_up_days: int = 0
    last_contact_at: datetime | None = None
    #: 关键日期：[{"label": "生日", "month": 5, "day": 12}]
    key_dates: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    interactions: list[InteractionSnapshot] = field(default_factory=list)


@dataclass(slots=True)
class FollowUp:
    contact_id: int
    name: str
    relation: str
    relation_label: str
    company: str = ""
    channel: str = ""
    due_at: datetime | None = None
    #: 正数=已逾期几天；负数=还有几天
    days_overdue: int = 0
    urgency: str = "later"  # overdue | today | soon | later
    reason: str = ""
    suggested: str = ""
    #: 他答应过但还没兑现的事
    promise: str = ""


def relation_label(key: str) -> str:
    return RELATIONS.get(str(key or ""), str(key or "其他"))


def resolve_cadence(contact: ContactSnapshot) -> int:
    """这个联系人该多久联系一次。"""
    if contact.follow_up_days and contact.follow_up_days > 0:
        return int(contact.follow_up_days)
    return DEFAULT_FOLLOW_UP_DAYS.get(contact.relation, DEFAULT_FOLLOW_UP_DAYS["other"])


def _latest_interaction(contact: ContactSnapshot) -> InteractionSnapshot | None:
    if not contact.interactions:
        return None
    return max(contact.interactions, key=lambda item: item.at)


def last_contact_of(contact: ContactSnapshot) -> datetime | None:
    latest = _latest_interaction(contact)
    if latest is not None:
        return latest.at
    return contact.last_contact_at


def outstanding_promise(contact: ContactSnapshot) -> str:
    """最近一条还没兑现的承诺（他答应过的）。

    只在**最近的**互动里找：更早的承诺通常已经被后来那条覆盖了，
    拿一条三个月前的承诺去催很奇怪。
    """
    latest = _latest_interaction(contact)
    if latest is not None and latest.promised:
        return latest.promised
    return ""


def next_follow_up(contact: ContactSnapshot, *, now: datetime) -> datetime | None:
    """下次该联系的时间。

    优先级：明确约定的时间 > 互动里留下的 next_follow_up_at > 上次联系 + 节奏。
    从未联系过时用录入时间当基准——"加进通讯录却没打过招呼"本身就该提醒。
    """
    explicit: list[datetime] = []
    if _latest_interaction(contact) is not None:
        latest = _latest_interaction(contact)
        if latest is not None and latest.next_follow_up_at is not None:
            explicit.append(latest.next_follow_up_at)
    for item in contact.interactions:
        if item.next_follow_up_at is not None and item.direction == "outbound":
            explicit.append(item.next_follow_up_at)

    future_explicit = [moment for moment in explicit if moment >= now]
    if future_explicit:
        return min(future_explicit)
    if explicit:
        # 约定的时间已经过了：那是"逾期"，用最近一个约定时间
        return max(explicit)

    base = last_contact_of(contact) or contact.created_at
    if base is None:
        return None
    return base + timedelta(days=resolve_cadence(contact))


def _urgency(days_overdue: int) -> str:
    if days_overdue > 0:
        return "overdue"
    if days_overdue == 0:
        return "today"
    if -days_overdue <= SOON_DAYS:
        return "soon"
    return "later"


def _reason(contact: ContactSnapshot, *, now: datetime, promise: str) -> str:
    last = last_contact_of(contact)
    cadence = resolve_cadence(contact)
    if last is None:
        return "加进通讯录之后还没打过招呼"

    days = max(0, int((now - last).days))
    parts: list[str] = []
    if promise:
        parts.append(f"他答应过「{promise}」，还没兑现")
    parts.append(f"上次沟通是 {days} 天前，超过你设的 {cadence} 天节奏" if days >= cadence else f"距上次沟通 {days} 天")
    return "；".join(parts)


def suggest_message(
    contact: ContactSnapshot,
    *,
    now: datetime,
    promise: str = "",
    purpose: str = "",
) -> str:
    """给一句可以直接发出去的开场。

    按关系分档：内推人要问进展、HR 要问流程、猎头要给条件、校友可以请教，
    通用关系就走"好久没联系"的路子。**不写空话**（"在吗"这种最容易被忽略）。
    """
    name = contact.name or "你好"
    purpose_note = f"（想请教的是：{purpose}）" if purpose else ""

    if promise:
        return (
            f"{name}你好，上次麻烦你帮忙的「{promise}」有消息了吗？"
            "不着急，只是想同步一下进度，有进展随时说一声就行。"
        )

    if contact.relation == "referral":
        return f"{name}你好，之前你帮我内推的岗位最近有进展吗？如果流程上有需要我补的材料，随时跟我说。"
    if contact.relation == "hr":
        return f"{name}你好，我是之前面试过的候选人。想确认一下这个岗位的流程大概什么时候会有反馈？谢谢！"
    if contact.relation == "headhunter":
        return (
            f"{name}你好，最近手上有合适的机会吗？我这边主要看中后台方向，"
            "薪资预期可以按市场行情谈，方便的话发我 JD 看看？"
        )
    if contact.relation == "alumni":
        return (
            f"{name}你好，我是 XX 届的校友，最近在看机会，想请教一下贵司的技术方向。"
            f"{purpose_note}不会占用太多时间，方便的话约个十分钟？"
        )
    if contact.relation == "interviewer":
        return f"{name}你好，上次面试聊得很受启发。想同步一下我最近的进展，也想请教两个技术问题，方便吗？"
    return f"{name}你好，好久没联系了，最近怎么样？我最近在看新的机会，想起你在这边，就来打个招呼。"


def relationship_health(contact: ContactSnapshot, *, now: datetime) -> dict[str, Any]:
    """一段关系的健康度（用于列表里的信号灯）。"""
    cadence = resolve_cadence(contact)
    last = last_contact_of(contact)
    if last is None:
        return {"level": "cold", "days_since": None, "ratio": 1.0}
    days = max(0, int((now - last).days))
    ratio = days / max(1, cadence)
    if ratio >= 1.5:
        level = "stale"
    elif ratio >= 1.0:
        level = "due"
    else:
        level = "warm"
    return {"level": level, "days_since": days, "ratio": round(ratio, 2)}


def greeting_for(contact: ContactSnapshot, *, now: datetime) -> str | None:
    """今天是不是该发问候（生日 / 固定节日）。"""
    for item in contact.key_dates or []:
        try:
            month = int(item.get("month") or 0)
            day = int(item.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if month == now.month and day == now.day:
            label = str(item.get("label") or "重要日子")
            return f"今天是{contact.name or '他'}的{label}，发一句祝福"

    for month, day, label, message in FIXED_GREETINGS:
        if month == now.month and day == now.day:
            return f"今天是{label}：{message}"
    return None


def build_follow_ups(
    contacts: Sequence[ContactSnapshot],
    *,
    now: datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    include_later: bool = False,
) -> list[FollowUp]:
    """列出该联系的人（默认只到 upcoming horizon 内，逾期的一定在内）。"""
    rows: list[FollowUp] = []
    horizon = now + timedelta(days=max(1, horizon_days))

    for contact in contacts:
        due = next_follow_up(contact, now=now)
        if due is None or (due > horizon and not include_later):
            continue

        days_overdue = (now.date() - due.date()).days
        promise = outstanding_promise(contact)
        rows.append(
            FollowUp(
                contact_id=contact.id,
                name=contact.name,
                relation=contact.relation,
                relation_label=relation_label(contact.relation),
                company=contact.company,
                channel=contact.channel,
                due_at=due,
                days_overdue=days_overdue,
                urgency=_urgency(days_overdue),
                reason=_reason(contact, now=now, promise=promise),
                suggested=suggest_message(contact, now=now, promise=promise),
                promise=promise,
            )
        )

    # 越逾期越靠前；同逾期天数时，内推人优先（他们的时效性最强）
    order = {"referral": 0, "interviewer": 1, "hr": 2, "headhunter": 3}
    rows.sort(key=lambda item: (-item.days_overdue, order.get(item.relation, 9), item.name))
    return rows


def timeline(interactions: Iterable[InteractionSnapshot]) -> list[dict[str, Any]]:
    """沟通时间线（倒序，最近的在前）。"""
    rows = [
        {
            "at": item.at,
            "direction": item.direction,
            "direction_label": DIRECTIONS.get(item.direction, item.direction),
            "kind": item.kind,
            "kind_label": INTERACTION_KINDS.get(item.kind, item.kind),
            "summary": item.summary,
            "promised": item.promised,
            "next_follow_up_at": item.next_follow_up_at,
        }
        for item in interactions
    ]
    rows.sort(key=lambda item: item["at"], reverse=True)
    return rows


def relation_options() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "default_follow_up_days": DEFAULT_FOLLOW_UP_DAYS.get(key, 14)}
        for key, label in RELATIONS.items()
    ]


def interaction_options() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label in INTERACTION_KINDS.items()]
