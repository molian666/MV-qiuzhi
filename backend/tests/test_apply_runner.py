"""半自动投递编排：候选生成 → 人工确认 → 限速执行 → 熔断落库。

这是 M10 最重要的测试文件：它要证明三件事
1. **没确认过的岗位不会投出去**（人工确认闸门）；
2. 投出去的会正确落成 application + 事件（后续漏斗/归因能直接用）；
3. 出现验证码/掉线信号时立刻停投，并且**当天不再投**。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlmodel import select

from app.apply.adapters import MockPlatformAdapter
from app.apply.adapters.base import JobCard, SearchQuery
from app.apply.breaker import BreakerSignal
from app.apply.filters import FilterRule
from app.apply.runner import (
    ApplyDisabledError,
    ApplySettings,
    approve_tasks,
    execute_approved,
    load_settings,
    queue_snapshot,
    save_settings,
    skip_tasks,
)
from app.apply.runner import search_and_queue
from app.apply.throttler import ThrottleConfig
from app.db import session_scope
from app.deps import get_llm
from app.models.apply import Application, ApplicationEvent, ApplyTask, JobPosting, PlatformAccount

RESUME_TEXT = (
    "五年后端开发经验。负责订单系统重构，熟悉 Python、Redis、MySQL 与消息队列，"
    "把核心接口 QPS 从 800 提升到 4000。"
)


def _settings(**overrides: Any) -> ApplySettings:
    base: dict[str, Any] = {
        "enabled": True,
        "risk_acknowledged": True,
        "platform": "mock",
        # 抖动设 0：测试不想等；真实默认值是 3–9 秒
        "throttle": ThrottleConfig(per_minute=5, per_day=30, jitter_min_seconds=0.0, jitter_max_seconds=0.0),
    }
    base.update(overrides)
    return ApplySettings(**base)


def _rules() -> list[FilterRule]:
    return [
        FilterRule(kind="blacklist", priority=10, name="黑名单"),
        FilterRule(kind="dedup", priority=20, name="去重"),
        FilterRule(kind="headhunter", params={"deny": True}, priority=30, name="猎头"),
        FilterRule(kind="hr_active", params={"require_active": True}, priority=40, name="HR活跃"),
        FilterRule(kind="salary", params={"min_salary": 20000, "max_salary": 80000}, priority=50, name="薪资"),
        FilterRule(kind="keyword", params={"deny": ["外包", "驻场"]}, priority=60, name="屏蔽词"),
    ]


async def _search(
    *,
    adapter: MockPlatformAdapter | None = None,
    settings: ApplySettings | None = None,
    keywords: list[str] | None = None,
):
    board = adapter or MockPlatformAdapter()
    with session_scope() as db:
        return await search_and_queue(
            db,
            adapter=board,
            query=SearchQuery(keywords=keywords or [], limit=20),
            rules=_rules(),
            resume_text=RESUME_TEXT,
            llm=get_llm(),
            settings=settings or _settings(),
        )


async def _execute(*, adapter: MockPlatformAdapter | None = None, settings: ApplySettings | None = None, **kwargs: Any):
    board = adapter or MockPlatformAdapter()
    with session_scope() as db:
        return await execute_approved(db, adapter=board, settings=settings or _settings(), **kwargs)


# --------------------------------------------------------------------------- #
# 候选生成
# --------------------------------------------------------------------------- #
def test_search_queues_passing_jobs_and_blocks_the_rest(client: Any) -> None:
    outcome = asyncio.run(_search())

    assert outcome.fetched == 3
    # 默认 Mock 里第三个是"外包/猎头/HR 不活跃/薪资偏低"，会被拦
    assert outcome.passed == 2
    assert outcome.blocked == 1
    assert outcome.queued == 2

    blocked = [item for item in outcome.candidates if not item.passed]
    assert len(blocked) == 1
    assert blocked[0].company == "某某外包"
    # 拦截理由是**具体**的，能在 UI 上说清为什么没投
    assert blocked[0].reasons


def test_blocked_job_still_gets_recorded_but_never_queued(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        postings = list(db.exec(select(JobPosting)).all())
        tasks = list(db.exec(select(ApplyTask)).all())

    assert len(postings) == 3  # 抓到的都留档（便于回看"它为什么不投"）
    assert len(tasks) == 2  # 但只有通过的进队列
    assert all(task.status == "pending" for task in tasks)


def test_search_is_idempotent_for_the_same_job(client: Any) -> None:
    first = asyncio.run(_search())
    second = asyncio.run(_search())

    assert first.queued == 2
    assert second.queued == 0
    assert second.duplicates == 2

    with session_scope() as db:
        assert len(list(db.exec(select(ApplyTask)).all())) == 2


def test_candidates_sorted_by_matched_score(client: Any) -> None:
    outcome = asyncio.run(_search())
    scores = [item.matched_score for item in outcome.candidates]
    assert scores == sorted(scores, reverse=True)


def test_say_hi_is_human_like(client: Any) -> None:
    from app.apply.say_hi import BANNED_OPENINGS, MAX_SAY_HI_CHARS

    outcome = asyncio.run(_search())
    queued = [item for item in outcome.candidates if item.passed]

    for item in queued:
        assert item.say_hi
        assert len(item.say_hi) <= MAX_SAY_HI_CHARS
        assert "\n" not in item.say_hi
        assert not any(item.say_hi.startswith(opening) for opening in BANNED_OPENINGS)


def test_say_hi_varies_with_job(client: Any) -> None:
    """招呼语真的读了 JD，而不是一句万用模板。"""
    outcome = asyncio.run(_search())
    texts = {item.say_hi for item in outcome.candidates if item.passed and item.say_hi}
    assert len(texts) > 1


def test_search_requires_enabled_module(client: Any) -> None:
    with pytest.raises(ApplyDisabledError):
        asyncio.run(_search(settings=_settings(enabled=False)))


# --------------------------------------------------------------------------- #
# 人工确认闸门
# --------------------------------------------------------------------------- #
def test_execute_refuses_when_nothing_approved(client: Any) -> None:
    asyncio.run(_search())
    outcome = asyncio.run(_execute())

    assert outcome.attempted == 0
    assert outcome.sent == 0
    assert "没有已确认" in outcome.stopped_reason

    with session_scope() as db:
        assert list(db.exec(select(Application)).all()) == []


def test_approve_then_execute_sends_and_records_application(client: Any) -> None:
    asyncio.run(_search())

    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
    assert len(task_ids) == 2

    with session_scope() as db:
        assert approve_tasks(db, task_ids=task_ids) == 2

    board = MockPlatformAdapter()
    outcome = asyncio.run(_execute(adapter=board))

    assert outcome.sent == 2
    assert outcome.failed == 0
    assert len(board.sent) == 2

    with session_scope() as db:
        applications = list(db.exec(select(Application)).all())
        tasks = list(db.exec(select(ApplyTask)).all())
        events = list(db.exec(select(ApplicationEvent)).all())
        account = db.exec(select(PlatformAccount).where(PlatformAccount.platform == "mock")).first()

    assert len(applications) == 2
    assert {app.source for app in applications} == {"apply_bot"}
    assert {app.status for app in applications} == {"applied"}
    assert all(app.applied_at is not None for app in applications)
    assert {task.status for task in tasks} == {"sent"}
    # 每条投递都有事件留痕（后续漏斗与响应时长统计依赖它）
    assert len(events) == 2
    assert account is not None and account.daily_sent == 2


def test_application_links_back_to_job_posting(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        approve_tasks(db, task_ids=task_ids)
    asyncio.run(_execute())

    with session_scope() as db:
        applications = list(db.exec(select(Application)).all())
        postings = {int(posting.id or 0) for posting in db.exec(select(JobPosting)).all()}

    assert all(app.job_posting_id in postings for app in applications)
    # 岗位的 JD 会带进投递记录，之后做归因/复盘时不用再抓一次
    assert all(app.jd_text for app in applications)


def test_skip_tasks_keeps_them_out_of_execution(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        assert skip_tasks(db, task_ids=task_ids[:1]) == 1

    outcome = asyncio.run(_execute())
    assert outcome.attempted == 0
    assert "没有已确认" in outcome.stopped_reason


def test_dry_run_does_not_send(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        approve_tasks(db, task_ids=task_ids)

    board = MockPlatformAdapter()
    outcome = asyncio.run(_execute(adapter=board, dry_run=True))

    assert outcome.attempted == 2
    assert outcome.sent == 0
    assert board.sent == []

    with session_scope() as db:
        assert [task.status for task in db.exec(select(ApplyTask)).all()] == ["approved", "approved"]


# --------------------------------------------------------------------------- #
# 熔断与限速
# --------------------------------------------------------------------------- #
def test_breaker_trips_on_captcha_and_stops_further_sends(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        approve_tasks(db, task_ids=task_ids)

    # 第一次发送就撞上验证码
    board = MockPlatformAdapter(
        fail_from_attempt=1,
        fail_signal=BreakerSignal(kind="captcha", detail="出现滑块验证"),
    )
    outcome = asyncio.run(_execute(adapter=board))

    assert outcome.tripped is True
    assert outcome.sent == 0
    assert outcome.failed == 1
    assert "验证" in outcome.breaker_message
    # 只试了一次就停 —— 不能"带病继续投"
    assert board.attempts == 1

    with session_scope() as db:
        account = db.exec(select(PlatformAccount).where(PlatformAccount.platform == "mock")).first()
        assert account is not None and account.circuit_broken_until is not None


def test_circuit_blocks_later_attempts_entirely(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        approve_tasks(db, task_ids=task_ids)

    asyncio.run(_execute(adapter=MockPlatformAdapter(fail_from_attempt=1)))

    second_board = MockPlatformAdapter()
    outcome = asyncio.run(_execute(adapter=second_board))

    assert outcome.tripped is True
    assert "熔断" in outcome.breaker_message
    assert second_board.attempts == 0  # 熔断期间适配器根本不会被调用


def test_login_lost_trips_but_page_changed_does_not(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        approve_tasks(db, task_ids=task_ids)

    outcome = asyncio.run(
        _execute(
            adapter=MockPlatformAdapter(
                fail_from_attempt=1,
                fail_signal=BreakerSignal(kind="page_changed", detail="找不到按钮"),
            )
        )
    )

    # 页面改版是我们的适配问题：停本轮，但**不**停当天
    assert outcome.tripped is False
    assert outcome.failed == 1
    assert "适配" in outcome.stopped_reason

    with session_scope() as db:
        account = db.exec(select(PlatformAccount).where(PlatformAccount.platform == "mock")).first()
        assert account is None or account.circuit_broken_until is None


def test_throttle_stops_at_per_minute_limit(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        task_ids = [int(task.id or 0) for task in db.exec(select(ApplyTask)).all()]
        approve_tasks(db, task_ids=task_ids)

    settings = _settings(
        throttle=ThrottleConfig(per_minute=1, per_day=30, jitter_min_seconds=0.0, jitter_max_seconds=0.0)
    )
    board = MockPlatformAdapter()
    outcome = asyncio.run(_execute(adapter=board, settings=settings))

    assert outcome.sent == 1
    assert len(board.sent) == 1
    assert "本分钟" in outcome.stopped_reason

    with session_scope() as db:
        # 没投的那条仍然是 approved（下次继续，不会丢）
        statuses = sorted(task.status for task in db.exec(select(ApplyTask)).all())
    assert statuses == ["approved", "sent"]


def test_execute_requires_enabled_module(client: Any) -> None:
    with pytest.raises(ApplyDisabledError):
        asyncio.run(_execute(settings=_settings(enabled=False)))


# --------------------------------------------------------------------------- #
# 设置与队列概览
# --------------------------------------------------------------------------- #
def test_settings_roundtrip_and_clamping(client: Any) -> None:
    with session_scope() as db:
        saved = save_settings(
            db,
            ApplySettings(
                enabled=True,
                risk_acknowledged=True,
                platform="boss",
                want_salary_min=25000,
                throttle=ThrottleConfig(per_minute=99, per_day=999, jitter_min_seconds=1.0, jitter_max_seconds=2.0),
            ),
        )
        assert saved.throttle.per_minute <= 10
        assert saved.throttle.per_day <= 60

        reloaded = load_settings(db)
        assert reloaded.enabled is True
        assert reloaded.platform == "boss"
        assert reloaded.want_salary_min == 25000


def test_settings_default_to_disabled(client: Any) -> None:
    with session_scope() as db:
        settings = load_settings(db)
    # 默认关闭：装完就能用 ≠ 装完就会替你投简历
    assert settings.enabled is False
    assert settings.risk_acknowledged is False


def test_queue_snapshot_counts(client: Any) -> None:
    asyncio.run(_search())
    with session_scope() as db:
        snapshot = queue_snapshot(db)

    assert snapshot["total"] == 2
    assert snapshot["pending"] == 2
    assert snapshot["sent"] == 0
