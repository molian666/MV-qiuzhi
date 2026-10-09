"""多账号轮换的选号逻辑（纯函数）。

**先说清楚这个能力能做什么、不能做什么**：

- 它能做：一个账号接近每日额度、或已经被风控熔断时，换另一个账号继续投。
- 它**不能**做：提高平台整体允许的投递量。平台的风控是跨账号看行为特征的，
  多账号只是把风险摊开，不是消除风险。所以单账号的限额仍然保守。

选号规则刻意做成**"用到接近限额才换"**，而不是轮流平均分：

1. 轮流平均分会让每个账号都"半活跃"，行为更不像真人，也让单账号的额度永远用不满——
   而额度和熔断本来就是按账号算的。
2. 集中用一个号，出问题时影响面清楚（就是那个号），排查容易。

`pick_profile` 返回 None 表示"现在一个可用的账号都没有"，调用方据此停投并说明原因——
**这比闷头用第一个账号更安全**：第一个账号可能正是刚被熔断的那个。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: 一个账号用到额度的这个比例，就优先换下一个（留出余量，不要正好撞上限）
DEFAULT_SWITCH_RATIO = 0.8


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """选号需要的账号状态（与 ORM 解耦，方便单测）。"""

    profile_id: int
    label: str = ""
    enabled: bool = True
    daily_sent: int = 0
    daily_limit: int = 30
    circuit_broken_until: datetime | None = None
    #: 上次用到它的时刻（用于"优先用最近用过的那个"）
    last_used_at: datetime | None = None
    #: 代理是否可用。**代理配错会让请求走真实 IP**，所以不可用就当这个号不可用。
    proxy_ok: bool = True

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self.daily_sent)


@dataclass(frozen=True, slots=True)
class ProfileChoice:
    """选号结果。`reason` 说明为什么选它 / 为什么一个都没选。"""

    profile: ProfileSnapshot | None
    reason: str = ""


def _is_broken(profile: ProfileSnapshot, now: datetime) -> bool:
    return profile.circuit_broken_until is not None and profile.circuit_broken_until > now


def usable_profiles(
    profiles: list[ProfileSnapshot],
    *,
    now: datetime,
    switch_ratio: float = DEFAULT_SWITCH_RATIO,
) -> list[ProfileSnapshot]:
    """现在哪些账号能用，按"优先继续用"排序。

    排序分两档：

    1. **还没到切换阈值的排前面**——它们还能继续用，优先继续用同一个号。
    2. **同一档里按最近用过的优先**（`last_used_at` 新的在前）。

    第 2 条不能省：只按剩余额度排的话，`daily_sent=5` 的号会输给 `daily_sent=0` 的号，
    于是**每投一条都在换号**——那正是"轮流平均分"那种不像真人的行为。
    最近用过的那个才是"当前正在用的号"，应当继续用它直到该换为止。
    """

    def sort_key(item: ProfileSnapshot) -> tuple[int, float, int]:
        used_ratio = item.daily_sent / item.daily_limit if item.daily_limit > 0 else 1.0
        # 0 = 还没到切换阈值（优先继续用），1 = 该换了
        needs_switch = 0 if used_ratio < switch_ratio else 1
        # 时间戳取负：越近的排越前。没记录过（None）当成很久以前。
        recency = -(item.last_used_at.timestamp() if item.last_used_at is not None else 0.0)
        return (needs_switch, recency, -item.remaining)

    usable = [
        item
        for item in profiles
        if item.enabled and item.remaining > 0 and not _is_broken(item, now) and item.proxy_ok
    ]
    return sorted(usable, key=sort_key)


def pick_profile(
    profiles: list[ProfileSnapshot],
    *,
    now: datetime,
    switch_ratio: float = DEFAULT_SWITCH_RATIO,
) -> ProfileChoice:
    """选一个账号投递。没有任何可用账号时返回 `ProfileChoice(None, 原因)`。"""
    if not profiles:
        return ProfileChoice(None, "还没有配置任何账号")

    usable = usable_profiles(profiles, now=now, switch_ratio=switch_ratio)
    if usable:
        chosen = usable[0]
        label = chosen.label or f"账号 {chosen.profile_id}"
        return ProfileChoice(chosen, f"使用 {label}（今日剩余 {chosen.remaining} 条）")

    # 一个都没得用：把原因拼出来，用户才知道该去做什么
    reasons: list[str] = []
    if all(not item.enabled for item in profiles):
        reasons.append("所有账号都被停用了")
    if any(_is_broken(item, now) for item in profiles):
        reasons.append("有账号正处在熔断期")
    if any(item.remaining <= 0 for item in profiles):
        reasons.append("有账号今日额度已用完")
    if any(not item.proxy_ok for item in profiles):
        reasons.append("有账号的代理不可用")

    detail = "、".join(dict.fromkeys(reasons)) or "没有可用账号"
    return ProfileChoice(None, f"{detail}，本次不投递")
