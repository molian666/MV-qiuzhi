"""多账号的存取与选号装配。

`profiles.py` 是纯判定（给定状态选哪个号），这里负责"从库里取状态、把代理与指纹
挂到账号上"。分开的理由与项目其它地方一致：判定逻辑要能脱离数据库单测。

代理与指纹存在 `AppSetting` 的 JSON 里（key 为 `apply_profile_meta`，
形如 `{"1": {"proxy": "...", "locale": "zh-CN"}}`），**这样加字段不用改表结构**
（与 `apply_settings` / `apply_schedule` 同一个约定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from ..clock import utcnow
from ..models.apply import PlatformProfile
from ..models.system import AppSetting
from .profiles import ProfileSnapshot, pick_profile
from .proxy import Fingerprint, fingerprint_for, localhost_warning, proxy_is_usable

META_KEY = "apply_profile_meta"

#: 每个账号默认的每日上限。**比单账号场景更保守**：多账号不等于平台允许更多总量，
#: 只是把风险摊开；把每个号都设成满额会让所有号一起变活跃。
DEFAULT_PROFILE_DAILY_LIMIT = 20


@dataclass(slots=True)
class ProfileMeta:
    """账号的代理与语言（存 JSON，不进表结构）。"""

    proxy: str = ""
    locale: str = "zh-CN"


def load_meta(db: Session) -> dict[str, ProfileMeta]:
    row = db.exec(select(AppSetting).where(AppSetting.key == META_KEY)).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    result: dict[str, ProfileMeta] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        result[str(key)] = ProfileMeta(
            proxy=str(value.get("proxy") or ""),
            locale=str(value.get("locale") or "zh-CN"),
        )
    return result


def save_meta(db: Session, meta: dict[str, ProfileMeta]) -> dict[str, ProfileMeta]:
    payload = {
        key: {"proxy": item.proxy.strip(), "locale": item.locale}
        for key, item in meta.items()
    }
    row = db.exec(select(AppSetting).where(AppSetting.key == META_KEY)).first()
    if row is None:
        db.add(AppSetting(key=META_KEY, value=payload))
    else:
        row.value = payload
        row.updated_at = utcnow()
        db.add(row)
    db.commit()
    return load_meta(db)


def set_profile_meta(db: Session, profile_id: int, *, proxy: str = "", locale: str = "zh-CN") -> None:
    meta = load_meta(db)
    meta[str(profile_id)] = ProfileMeta(proxy=proxy, locale=locale)
    save_meta(db, meta)


def list_profiles(db: Session, *, platform: str = "") -> list[PlatformProfile]:
    statement = select(PlatformProfile).order_by(PlatformProfile.id)  # type: ignore[arg-type]
    if platform:
        statement = statement.where(PlatformProfile.platform == platform)
    return list(db.exec(statement).all())


def create_profile(
    db: Session,
    *,
    platform: str = "boss",
    label: str = "",
    user_data_dir: str = "",
    daily_limit: int = DEFAULT_PROFILE_DAILY_LIMIT,
    proxy: str = "",
    locale: str = "zh-CN",
) -> PlatformProfile:
    """新建一个账号。

    **`user_data_dir` 必须各自独立**：共用目录等于共用一个登录态，多账号就是假的，
    而且两个号的 cookie 会互相覆盖。所以没给目录时按 profile id 自动生成。
    """
    profile = PlatformProfile(
        platform=platform,
        label=label.strip(),
        user_data_dir=user_data_dir.strip(),
        daily_limit=max(1, int(daily_limit)),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    # 目录留空时按 id 补一个（这时才有 id 可用）
    if not profile.user_data_dir:
        profile.user_data_dir = f"browser/{platform}/profile-{profile.id}"
        db.add(profile)
        db.commit()
        db.refresh(profile)

    set_profile_meta(db, int(profile.id or 0), proxy=proxy, locale=locale)
    return profile


def profile_snapshot(db: Session, profile: PlatformProfile, *, now: datetime | None = None) -> ProfileSnapshot:
    """把一条账号记录转成选号用的快照（含代理可用性与今日用量）。"""
    meta = load_meta(db).get(str(profile.id), ProfileMeta())
    # 没配代理视为直连（可用）；配了但不可用则整条账号不可用——别裸奔
    proxy_ok = True if not meta.proxy else proxy_is_usable(meta.proxy)
    return ProfileSnapshot(
        profile_id=int(profile.id or 0),
        label=profile.label or f"账号 {profile.id}",
        enabled=profile.enabled,
        # 今日用量按账号记账；用 0 会让"额度已用完"永远不成立，多账号就白做了
        daily_sent=profile_usage_today(db, int(profile.id or 0)),
        daily_limit=profile.daily_limit,
        circuit_broken_until=profile.circuit_broken_until,
        last_used_at=profile.last_used_at,
        proxy_ok=proxy_ok,
    )


@dataclass(slots=True)
class ProfileChoiceResult:
    """选号结果 + 这个账号的代理与指纹（调用方拿去建适配器）。"""

    profile: PlatformProfile | None
    proxy: str = ""
    fingerprint: Fingerprint | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def choose_profile(
    db: Session,
    *,
    platform: str = "boss",
    now: datetime | None = None,
) -> ProfileChoiceResult:
    """按 `profiles.pick_profile` 选一个账号，并带上它的代理与指纹。

    一个账号都没配时返回 `profile=None` 且原因是"还没配置任何账号"——
    调用方据此回落到单账号模式（用平台默认的浏览器目录），而不是直接报错：
    **多账号是可选能力，没配它不该让整个投递不可用**。
    """
    moment = now or utcnow()
    profiles = list_profiles(db, platform=platform)
    if not profiles:
        return ProfileChoiceResult(profile=None, reason="还没有配置任何账号（使用默认账号）")

    snapshots = {item.profile_id: item for item in (profile_snapshot(db, row, now=moment) for row in profiles)}
    choice = pick_profile(list(snapshots.values()), now=moment)

    if choice.profile is None:
        return ProfileChoiceResult(profile=None, reason=choice.reason)

    chosen = next((row for row in profiles if int(row.id or 0) == choice.profile.profile_id), None)
    if chosen is None:  # pragma: no cover - 快照与记录同源，理论上不会发生
        return ProfileChoiceResult(profile=None, reason="选中的账号已不存在")

    meta = load_meta(db).get(str(chosen.id), ProfileMeta())
    warnings: list[str] = []
    if meta.proxy:
        hint = localhost_warning(meta.proxy)
        if hint:
            warnings.append(hint)

    return ProfileChoiceResult(
        profile=chosen,
        proxy=meta.proxy,
        fingerprint=fingerprint_for(int(chosen.id or 0), locale=meta.locale),
        reason=choice.reason,
        warnings=warnings,
    )


def mark_profile_used(db: Session, profile_id: int, *, at: datetime | None = None) -> None:
    """记下"刚用过这个账号"——选号靠它实现"用到接近限额才换"。"""
    profile = db.get(PlatformProfile, profile_id)
    if profile is None:
        return
    profile.last_used_at = at or utcnow()
    db.add(profile)
    db.commit()


def trip_profile(db: Session, profile_id: int, *, until: datetime) -> None:
    """按账号熔断（A 号被风控不该让 B 号也停）。"""
    profile = db.get(PlatformProfile, profile_id)
    if profile is None:
        return
    profile.circuit_broken_until = until
    db.add(profile)
    db.commit()


def bump_profile_usage(db: Session, profile_id: int) -> None:
    """这个账号今天多投了一条。

    额度按账号算，所以需要一个能持久化的计数。存在 `PlatformProfile.note` 里会让它
    变成"混合用途字段"——所以这里用一个独立的 AppSetting 记账（按日期）。
    跨天自动归零：日期一变，旧记录自然失效。
    """
    today = utcnow().date().isoformat()
    row = db.exec(select(AppSetting).where(AppSetting.key == "apply_profile_usage")).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if str(data.get("date")) != today:
        data = {"date": today, "counts": {}}
    counts = dict(data.get("counts") or {})
    key = str(profile_id)
    counts[key] = int(counts.get(key, 0)) + 1
    data["counts"] = counts

    if row is None:
        db.add(AppSetting(key="apply_profile_usage", value=data))
    else:
        row.value = data
        row.updated_at = utcnow()
        db.add(row)
    db.commit()


def profile_usage_today(db: Session, profile_id: int) -> int:
    today = utcnow().date().isoformat()
    row = db.exec(select(AppSetting).where(AppSetting.key == "apply_profile_usage")).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if str(data.get("date")) != today:
        return 0
    counts = data.get("counts") or {}
    return int(counts.get(str(profile_id), 0))
