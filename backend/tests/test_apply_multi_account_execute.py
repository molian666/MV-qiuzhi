"""多账号接进投递执行路径。

这里要守住三件事：

1. **能力没开时行为与引入多账号之前完全一致**（不多选号、不换目录）。
2. **开了但一个可用账号都没有时明确拒绝**，而不是悄悄回落到默认账号——
   后者会绕开"按账号记额度与熔断"的全部保护。
3. **用量真的记到那个账号上**，否则"额度已用完"永远不成立、轮换就是空壳。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from app.apply.capabilities import CAPABILITIES, TIER_CONFIRM_PHRASES, Tier
from app.apply.profile_store import (
    bump_profile_usage,
    create_profile,
    list_profiles,
    profile_usage_today,
    trip_profile,
)
from app.db import session_scope
from app.models.apply import PlatformProfile

#: 模型用 `UTCDateTime`，**拒绝 naive datetime 与字符串**。远处的时间表示"一直熔断着"。
FAR_FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)

SETTINGS_URL = "/api/apply/settings"
SEARCH_URL = "/api/apply/search"
EXECUTE_URL = "/api/apply/execute"


#: 账号建在 `mock` 上，与设置里的平台保持一致。
#:
#: **不要为了"真实一点"把平台改成 boss**：那会让抓取走真实浏览器适配器，
#: 而 Boss 的选择器至今未校准，于是所有用例都会在"抓候选"这一步 502——
#: 失败原因看起来像多账号坏了，实际是平台选错了。选号是按平台过滤的，两边必须一致。
PROFILE_PLATFORM = "mock"


def _enable(client: Any, keys: list[str], phrases: list[str] | None = None) -> None:
    enabled = {item.key: False for item in CAPABILITIES}
    for key in keys:
        enabled[key] = True
    response = client.put(
        SETTINGS_URL,
        json={
            "enabled": True,
            "risk_acknowledged": True,
            "platform": PROFILE_PLATFORM,
            "capabilities": {"enabled": enabled, "signed_phrases": phrases or []},
        },
    )
    assert response.status_code == 200, response.text


def _queue_and_approve(client: Any) -> None:
    search = client.post(SEARCH_URL, json={"keywords": [], "limit": 5})
    assert search.status_code == 200, search.text
    ids = [item["task_id"] for item in search.json()["candidates"] if item["task_id"]]
    approved = client.post("/api/apply/queue/approve", json={"task_ids": ids})
    assert approved.status_code == 200


# --------------------------------------------------------------------------- #
# 能力没开：与以前完全一样
# --------------------------------------------------------------------------- #
def test_multi_account_off_ignores_configured_profiles(client: Any) -> None:
    """默认关闭时，即使库里配了账号也不选号——行为与引入多账号之前一致。"""
    with session_scope() as db:
        create_profile(db, platform=PROFILE_PLATFORM, label="不该被用到")

    _enable(client, [])  # 一个能力都不开
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 200
    assert response.json()["profile_label"] == ""


def test_multi_account_off_does_not_record_usage(client: Any) -> None:
    with session_scope() as db:
        profile = create_profile(db, platform=PROFILE_PLATFORM)
        profile_id = int(profile.id or 0)

    _enable(client, [])
    _queue_and_approve(client)
    client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    with session_scope() as db:
        assert profile_usage_today(db, profile_id) == 0


# --------------------------------------------------------------------------- #
# 能力开了：选号并记账
# --------------------------------------------------------------------------- #
def test_multi_account_on_picks_a_profile(client: Any) -> None:
    with session_scope() as db:
        create_profile(db, platform=PROFILE_PLATFORM, label="主号")

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 200, response.text
    assert response.json()["profile_label"] == "主号"


def test_multi_account_records_usage_on_the_chosen_profile(client: Any) -> None:
    """**用量记不到账号上，轮换就是空壳**：额度永远不减，也就永远不切换。"""
    with session_scope() as db:
        profile = create_profile(db, platform=PROFILE_PLATFORM, label="主号", daily_limit=10)
        profile_id = int(profile.id or 0)

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 2, "action": "greet"})
    assert response.status_code == 200
    sent = response.json()["sent"]
    assert sent >= 1

    with session_scope() as db:
        assert profile_usage_today(db, profile_id) == sent


def test_dry_run_does_not_record_usage(client: Any) -> None:
    """演练模式什么都没发出去，不该占额度。"""
    with session_scope() as db:
        profile = create_profile(db, platform=PROFILE_PLATFORM, label="主号")
        profile_id = int(profile.id or 0)

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 2, "dry_run": True, "action": "greet"})
    assert response.status_code == 200

    with session_scope() as db:
        assert profile_usage_today(db, profile_id) == 0


def test_marks_the_profile_as_used(client: Any) -> None:
    """记下"刚用过"，选号才能实现"用到接近限额才换"而不是每条都换。"""
    with session_scope() as db:
        profile = create_profile(db, platform=PROFILE_PLATFORM, label="主号")
        profile_id = int(profile.id or 0)

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)
    client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    with session_scope() as db:
        saved = db.get(PlatformProfile, profile_id)

    assert saved is not None
    assert saved.last_used_at is not None


# --------------------------------------------------------------------------- #
# 一个可用账号都没有：明确拒绝，不偷偷回落到默认账号
# --------------------------------------------------------------------------- #
def test_no_usable_profile_is_refused_not_silently_defaulted(client: Any) -> None:
    """**关键**：悄悄回落到默认账号会绕开按账号记额度与熔断的全部保护。"""
    with session_scope() as db:
        profile = create_profile(db, platform=PROFILE_PLATFORM, label="被熔断的号")
        trip_profile(db, int(profile.id or 0), until=FAR_FUTURE)

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 409
    assert "熔断" in response.json()["detail"]


def test_no_profiles_configured_is_refused_when_capability_is_on(client: Any) -> None:
    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 409
    assert "还没有配置" in response.json()["detail"]


def test_exhausted_profile_is_refused(client: Any) -> None:
    """额度用完的账号不能再投——这条以前不会成立，因为用量没记上。"""
    with session_scope() as db:
        profile = create_profile(db, platform=PROFILE_PLATFORM, label="满额的号", daily_limit=2)
        profile_id = int(profile.id or 0)
        bump_profile_usage(db, profile_id)
        bump_profile_usage(db, profile_id)

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 409
    assert "额度" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 坏代理：账号不可用
# --------------------------------------------------------------------------- #
def test_profile_with_broken_proxy_is_refused(client: Any) -> None:
    """代理配错会让请求走真实 IP，所以那个账号直接视为不可用。"""
    with session_scope() as db:
        create_profile(db, platform=PROFILE_PLATFORM, label="坏代理的号", proxy="not-a-proxy")

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 409
    assert "代理" in response.json()["detail"]


def test_localhost_proxy_warning_is_surfaced(client: Any) -> None:
    """指向本机的代理不会改变出口 IP——要提醒用户，否则他以为换好了。"""
    with session_scope() as db:
        create_profile(db, platform=PROFILE_PLATFORM, label="本机代理", proxy="http://127.0.0.1:7890")

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 200, response.text
    assert any("出口 IP 不会改变" in note for note in response.json()["notes"])


# --------------------------------------------------------------------------- #
# 轮换真的发生
# --------------------------------------------------------------------------- #
def test_rotation_moves_to_the_next_profile_when_one_is_full(client: Any) -> None:
    """**这就是多账号的意义**：A 号满了，这一轮换 B 号。"""
    with session_scope() as db:
        first = create_profile(db, platform=PROFILE_PLATFORM, label="A", daily_limit=1)
        create_profile(db, platform=PROFILE_PLATFORM, label="B", daily_limit=10)
        bump_profile_usage(db, int(first.id or 0))  # A 号额度已满

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 200, response.text
    assert response.json()["profile_label"] == "B"


def test_tripped_profile_is_skipped_in_favour_of_another(client: Any) -> None:
    """A 号被风控不该让 B 号也停。"""
    with session_scope() as db:
        first = create_profile(db, platform=PROFILE_PLATFORM, label="A")
        create_profile(db, platform=PROFILE_PLATFORM, label="B")
        trip_profile(db, int(first.id or 0), until=FAR_FUTURE)

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 200, response.text
    assert response.json()["profile_label"] == "B"


def test_disabled_profile_is_not_chosen(client: Any) -> None:
    with session_scope() as db:
        first = create_profile(db, platform=PROFILE_PLATFORM, label="停用的号")
        first.enabled = False
        db.add(first)
        db.commit()
        create_profile(db, platform=PROFILE_PLATFORM, label="可用号")

    _enable(client, ["multi_account"], phrases=[TIER_CONFIRM_PHRASES[Tier.T2], TIER_CONFIRM_PHRASES[Tier.T3]])
    _queue_and_approve(client)

    response = client.post(EXECUTE_URL, json={"limit": 1, "action": "greet"})

    assert response.status_code == 200
    assert response.json()["profile_label"] == "可用号"
