"""M17：投递动作拆分——打招呼（greet）与投递简历（resume）。

要证明三件事：
1. 打招呼只推进状态到 greeted、不建投递记录、不占每日额度；
2. 投递简历对"已打招呼"的任务发简历（带附件）并落一次 Application；
3. 打招呼也是真实发送动作，同样受限速约束（不会连发）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlmodel import select

from app.apply.adapters import MockPlatformAdapter
from app.apply.adapters.base import JobCard, SearchQuery, SendResult
from app.apply.filters import FilterRule
from app.apply.runner import (
    ApplySettings,
    approve_tasks,
    execute_approved,
    load_settings,
    save_settings,
    search_and_queue,
)
from app.apply.throttler import ThrottleConfig
from app.db import session_scope
from app.deps import get_llm
from app.models.apply import Application, ApplyTask, PlatformAccount

RESUME_TEXT = (
    "五年后端开发经验。负责订单系统重构，熟悉 Python、Redis、MySQL 与消息队列，"
    "把核心接口 QPS 从 800 提升到 4000。"
)


def _settings(**overrides: Any) -> ApplySettings:
    base: dict[str, Any] = {
        "enabled": True,
        "risk_acknowledged": True,
        "platform": "mock",
        "throttle": ThrottleConfig(per_minute=5, per_day=30, jitter_min_seconds=0.0, jitter_max_seconds=0.0),
    }
    base.update(overrides)
    return ApplySettings(**base)


def _rules() -> list[FilterRule]:
    return [
        FilterRule(kind="blacklist", priority=10, name="黑名单"),
        FilterRule(kind="dedup", priority=20, name="去重"),
        FilterRule(kind="salary", params={"min_salary": 20000, "max_salary": 80000}, priority=50, name="薪资"),
    ]


async def _search() -> list[int]:
    board = MockPlatformAdapter()
    with session_scope() as db:
        outcome = await search_and_queue(
            db,
            adapter=board,
            query=SearchQuery(keywords=[], limit=20),
            rules=_rules(),
            resume_text=RESUME_TEXT,
            llm=get_llm(),
            settings=_settings(),
        )
        return [item.task_id for item in outcome.candidates if item.task_id is not None]


async def _approve(task_ids: list[int]) -> None:
    with session_scope() as db:
        approve_tasks(db, task_ids=task_ids)


async def _execute(
    *,
    adapter: MockPlatformAdapter | None = None,
    settings: ApplySettings | None = None,
    **kwargs: Any,
):
    """执行投递。

    `settings` 不传时**以库里的设置为底、再叠上测试要的确定性参数**，而不是凭空造一份：
    能力开关持久化在库里，用一份全新的默认值会让"我在测试里改了设置"静默不生效。
    抖动设成 0 是为了让用例不真的等 3–9 秒。
    """
    board = adapter or MockPlatformAdapter()
    with session_scope() as db:
        if settings is None:
            active = load_settings(db)
            active.enabled = True
            active.risk_acknowledged = True
            active.capabilities.risk_acknowledged = True
            active.throttle = ThrottleConfig(
                per_minute=5, per_day=30, jitter_min_seconds=0.0, jitter_max_seconds=0.0
            )
        else:
            active = settings
        return await execute_approved(db, adapter=board, settings=active, **kwargs)


class _RecordingAdapter(MockPlatformAdapter):
    """记录每次发的是什么，验证「打招呼」与「投递简历」两个动作的区别。

    **M18 起断言 `resume_calls` 而不是 `sent_calls`**：附件以前是作为 `resume_image`
    参数塞给 `send_greeting` 的，而真实浏览器适配器收下这个参数后**从没用过**——
    于是"投递简历"其实只发了一句招呼语，测试却因为 Mock 会把它拼进文案而全绿。
    现在附件走独立的 `send_resume`，所以这里也必须盯那个出口。
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent_calls: list[tuple[str, str | None]] = []
        self.resume_calls: list[tuple[str, str | None]] = []

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None) -> SendResult:
        self.sent_calls.append((card.external_id, resume_image))
        return await super().send_greeting(card, text, resume_image)

    async def send_resume(self, card: JobCard, resume_image: str | None) -> SendResult:
        self.resume_calls.append((card.external_id, resume_image))
        return await super().send_resume(card, resume_image)


# --------------------------------------------------------------------------- #
# 打招呼动作
# --------------------------------------------------------------------------- #
def test_greet_only_moves_task_to_greeted_without_application(client: Any) -> None:
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    board = MockPlatformAdapter()
    outcome = asyncio.run(_execute(adapter=board, action="greet"))

    assert outcome.sent == 2
    assert outcome.failed == 0
    assert len(board.sent) == 2  # 真的发出去了

    with session_scope() as db:
        tasks = list(db.exec(select(ApplyTask)).all())
        applications = list(db.exec(select(Application)).all())
        account = db.exec(select(PlatformAccount).where(PlatformAccount.platform == "mock")).first()

    assert all(task.status == "greeted" for task in tasks)
    assert all(task.greeted_at is not None for task in tasks)
    assert all(task.sent_at is None for task in tasks)
    # 打招呼不落投递记录、不占每日额度（额度只算真正投出去的简历）
    assert applications == []
    assert account is None or account.daily_sent == 0


def test_greet_does_not_touch_already_greeted_tasks(client: Any) -> None:
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    asyncio.run(_execute(action="greet"))
    # 再打一次招呼：已 greeted 的任务不该被重复处理
    outcome = asyncio.run(_execute(action="greet"))

    assert outcome.attempted == 0
    assert "没有已确认待打招呼" in outcome.stopped_reason


def test_resume_sends_attachment_through_send_resume(client: Any) -> None:
    """投递简历 = 打招呼 + **真的把简历发出去**（走 `send_resume`）。

    附件要过 `resume_attachment` 能力闸门：这是"默认不做额外动作"的一部分。
    所以这里显式把能力打开，否则发出去的是平台的在线简历（另有一条用例覆盖）。
    """
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    board = MockPlatformAdapter()
    asyncio.run(_execute(adapter=board, action="greet"))

    # 给任务挂上简历附件，并把「投递时附带简历附件」能力打开
    with session_scope() as db:
        tasks = list(db.exec(select(ApplyTask)).all())
        for task in tasks:
            task.resume_image_path = "data/uploads/resumes/2_王工简历.pdf"
            db.add(task)
        settings = load_settings(db)
        settings.capabilities.enabled["resume_attachment"] = True
        save_settings(db, settings)

    recorder = _RecordingAdapter()
    outcome = asyncio.run(_execute(adapter=recorder, action="resume"))

    assert outcome.sent == 2
    assert outcome.failed == 0
    # 附件走的是 send_resume，**不是** send_greeting 的那个参数
    assert len(recorder.resume_calls) == 2
    assert all(image is not None for _, image in recorder.resume_calls)
    assert all(image is None for _, image in recorder.sent_calls), "招呼语那一步不该带附件"

    with session_scope() as db:
        tasks = list(db.exec(select(ApplyTask)).all())
        applications = list(db.exec(select(Application)).all())
        account = db.exec(select(PlatformAccount).where(PlatformAccount.platform == "mock")).first()

    assert all(task.status == "sent" for task in tasks)
    assert len(applications) == 2
    assert {app.source for app in applications} == {"apply_bot"}
    assert account is not None and account.daily_sent == 2


def test_resume_without_capability_sends_online_resume_and_says_so(client: Any) -> None:
    """没开附件能力时：仍然投递，但发的是平台在线简历，并且**在结果里写明**。

    这一条是"不能假装做到了"的落地：用户不会以为附件发出去了。
    """
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    with session_scope() as db:
        for task in db.exec(select(ApplyTask)).all():
            task.resume_image_path = "data/uploads/resumes/2_王工简历.pdf"
            db.add(task)
        db.commit()

    recorder = _RecordingAdapter()
    outcome = asyncio.run(_execute(adapter=recorder, action="resume"))

    assert outcome.sent == 2
    # 附件没发出去（能力没开）
    assert all(image is None for _, image in recorder.resume_calls)
    # 但必须明确告诉用户发的是在线简历
    assert any("在线简历" in item.detail for item in outcome.items)
    assert any("未开启" in item.detail for item in outcome.items)


def test_resume_failure_does_not_record_an_application(client: Any) -> None:
    """招呼语发出去了但简历失败 → **不能**记成已投递。

    否则用户会以为简历已送达，实际对方只看到一句问候。
    """
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    with session_scope() as db:
        for task in db.exec(select(ApplyTask)).all():
            task.resume_image_path = "data/uploads/resumes/2_王工简历.pdf"
            db.add(task)
        settings = load_settings(db)
        settings.capabilities.enabled["resume_attachment"] = True
        save_settings(db, settings)

    class _ResumeFails(MockPlatformAdapter):
        async def send_resume(self, card: JobCard, resume_image: str | None) -> SendResult:
            return SendResult(ok=False, detail="选择器失效")

    outcome = asyncio.run(_execute(adapter=_ResumeFails(), action="resume"))

    assert outcome.failed == 1
    assert outcome.sent == 0
    assert "简历没有发出去" in outcome.items[0].detail

    with session_scope() as db:
        tasks = list(db.exec(select(ApplyTask)).all())
        applications = list(db.exec(select(Application)).all())

    # 任务落成 failed（可重试），并且**没有**产生投递记录
    assert any(task.status == "failed" for task in tasks)
    assert applications == []


def test_resume_default_action_still_works_without_greeting(client: Any) -> None:
    """向后兼容：不传 action（默认 resume）时，approved 任务直接投递并落库。"""
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    board = MockPlatformAdapter()
    outcome = asyncio.run(_execute(adapter=board))

    assert outcome.sent == 2
    with session_scope() as db:
        assert len(list(db.exec(select(Application)).all())) == 2


def test_greet_counts_towards_per_minute_throttle(client: Any) -> None:
    """打招呼是真实发送动作：per_minute=1 时打过招呼，立即投简历会被限速拦住。"""
    task_ids = asyncio.run(_search())
    asyncio.run(_approve(task_ids))

    tight = _settings(throttle=ThrottleConfig(per_minute=1, per_day=30, jitter_min_seconds=0.0, jitter_max_seconds=0.0))
    board = MockPlatformAdapter()
    with session_scope() as db:
        first = asyncio.run(execute_approved(db, adapter=board, settings=tight, action="greet"))
    # per_minute=1：第一条打招呼发出后，第二条立刻被限速拦在本轮
    assert first.sent == 1
    assert "需等待" in first.stopped_reason

    second = asyncio.run(_execute(adapter=board, settings=tight, action="resume"))
    assert second.attempted == 0
    assert second.sent == 0
    assert second.stopped_reason  # 被限速挡下（原因文案来自 throttler）


def test_unknown_action_is_rejected(client: Any) -> None:
    with pytest.raises(ValueError):
        asyncio.run(_execute(action="send_spam"))
