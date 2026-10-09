"""定时投递的后台循环：防重入、异常不能弄死循环、闸门在动手前校验。

时间计算在 `test_apply_schedule.py`、通过闸门的策略在 `test_apply_autopilot.py`；
这里管的是"循环本身是否可靠"——一个静默死掉的后台任务是最难查的故障。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.apply import scheduler
from app.apply.capabilities import CAPABILITIES, TIER_CONFIRM_PHRASES, Tier
from app.apply.runner import load_settings, save_settings
from app.apply.schedule_store import MODE_APPLY, MODE_QUEUE, ScheduleSettings, load_schedule, save_schedule
from app.apply.scheduler import TickResult, reset_state, run_once, state, tick
from app.db import session_scope

SETTINGS_URL = "/api/apply/settings"


@pytest.fixture(autouse=True)
def _fresh_state() -> None:
    """每个用例都从干净的进程内状态开始（`running` 标记是模块级的）。"""
    reset_state()


def _enable(client: Any, keys: list[str], phrases: list[str] | None = None) -> None:
    enabled = {item.key: False for item in CAPABILITIES}
    for key in keys:
        enabled[key] = True
    response = client.put(
        SETTINGS_URL,
        json={
            "enabled": True,
            "risk_acknowledged": True,
            "capabilities": {"enabled": enabled, "signed_phrases": phrases or []},
        },
    )
    assert response.status_code == 200, response.text


def _enable_module() -> None:
    """把投递模块本身打开。

    排期到点之后走的是 `search_and_queue`，它有自己的 `ensure_enabled` 闸门
    （模块没开、或没确认风险就拒绝）。这里显式打开，否则每条用例都只会撞到那个闸门，
    测不到真正想测的东西。
    """
    with session_scope() as db:
        settings = load_settings(db)
        settings.enabled = True
        settings.risk_acknowledged = True
        settings.capabilities.risk_acknowledged = True
        save_settings(db, settings)


def _set_schedule(*, times: list[str], mode: str = MODE_QUEUE, enabled: bool = True) -> None:
    with session_scope() as db:
        save_schedule(db, ScheduleSettings(enabled=enabled, times=times, mode=mode, limit=3))


class _FakeAdapter:
    """假适配器：不碰浏览器，只记下被调用了。"""

    name = "mock"

    def __init__(self) -> None:
        self.searches = 0
        self.closed = 0

    async def search(self, query: Any) -> list[Any]:
        self.searches += 1
        return []

    async def fetch_detail(self, card: Any) -> Any:
        return card

    async def send_greeting(self, card: Any, text: str, resume_image: str | None = None) -> Any:
        raise AssertionError("本轮不该真的发送任何招呼语")

    async def send_resume(self, card: Any, resume_image: str | None) -> Any:
        raise AssertionError("本轮不该真的发送任何简历")

    async def close(self) -> None:
        self.closed += 1


# --------------------------------------------------------------------------- #
# 到期判定
# --------------------------------------------------------------------------- #
async def test_does_not_run_when_schedule_is_off(client: Any) -> None:
    _enable_module()
    _set_schedule(times=["09:00"], enabled=False)
    adapter = _FakeAdapter()

    result = await run_once(
        now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object()
    )

    assert result.ran is False
    assert adapter.searches == 0


async def test_runs_when_due(client: Any) -> None:
    _enable_module()
    _set_schedule(times=["09:00"])
    adapter = _FakeAdapter()

    result = await run_once(
        now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object()
    )

    assert result.ran is True
    assert result.mode == MODE_QUEUE
    assert adapter.searches == 1


async def test_does_not_run_before_the_time(client: Any) -> None:
    _enable_module()
    _set_schedule(times=["09:00"])
    adapter = _FakeAdapter()

    result = await run_once(
        now=datetime(2026, 9, 21, 8, 30), adapter_factory=lambda _s: adapter, llm=object()
    )

    assert result.ran is False
    assert adapter.searches == 0


async def test_does_not_run_twice_on_the_same_day(client: Any) -> None:
    _enable_module()
    _set_schedule(times=["09:00"])
    adapter = _FakeAdapter()

    first = await run_once(
        now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object()
    )
    second = await run_once(
        now=datetime(2026, 9, 21, 9, 5), adapter_factory=lambda _s: adapter, llm=object()
    )

    assert first.ran is True
    assert second.ran is False
    assert adapter.searches == 1, "同一天不该跑第二次"


async def test_run_is_recorded(client: Any) -> None:
    _enable_module()
    _set_schedule(times=["09:00"])
    adapter = _FakeAdapter()

    await run_once(now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object())

    with session_scope() as db:
        saved = load_schedule(db)

    assert saved.last_run_date is not None
    assert saved.last_run_at is not None


async def test_adapter_is_always_closed(client: Any) -> None:
    """浏览器是重资源，用完必须关——留着会占住 profile 目录。"""
    _enable_module()
    _set_schedule(times=["09:00"])
    adapter = _FakeAdapter()

    await run_once(now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object())

    assert adapter.closed == 1


async def test_adapter_is_closed_even_when_search_explodes(client: Any) -> None:
    """搜索抛异常时也必须关掉适配器，否则用户下次手动抓取会撞上「目录被占用」。"""

    class _Exploding(_FakeAdapter):
        async def search(self, query: Any) -> list[Any]:
            raise RuntimeError("抓取炸了")

    _enable_module()
    _set_schedule(times=["09:00"])
    adapter = _Exploding()

    with pytest.raises(RuntimeError):
        await run_once(
            now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object()
        )

    assert adapter.closed == 1


# --------------------------------------------------------------------------- #
# 闸门在动手之前校验
# --------------------------------------------------------------------------- #
async def test_auto_mode_is_downgraded_when_capability_was_turned_off(client: Any) -> None:
    """**关键**：用户可能在设完排期之后把自动投递关掉。

    那时必须降级成只入队并留下说明，而不是照旧投出去。
    """
    _enable_module()
    # 直接写库绕过 API 的闸门，模拟"设好排期之后把能力关掉了"
    _set_schedule(times=["09:00"], mode=MODE_APPLY)
    adapter = _FakeAdapter()

    result = await run_once(
        now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object()
    )

    assert result.mode == MODE_QUEUE, "没开自动投递时必须降级"
    assert "降级" in result.note
    assert result.sent == 0


async def test_auto_mode_runs_when_capability_is_open(client: Any) -> None:
    _enable_module()
    _enable(
        client,
        ["scheduled_queue", "auto_apply", "scheduled_apply"],
        phrases=[TIER_CONFIRM_PHRASES[Tier.T2]],
    )
    _set_schedule(times=["09:00"], mode=MODE_APPLY)
    adapter = _FakeAdapter()

    result = await run_once(
        now=datetime(2026, 9, 21, 9, 1), adapter_factory=lambda _s: adapter, llm=object()
    )

    assert result.mode == MODE_APPLY


# --------------------------------------------------------------------------- #
# 循环的可靠性
# --------------------------------------------------------------------------- #
async def test_tick_survives_an_exception(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """**一个后台任务静默死掉是这类功能最难查的故障。**

    异常必须被吞掉并记录，循环继续等下一个时间点。
    """

    async def exploding(**kwargs: Any) -> TickResult:
        raise RuntimeError("内部炸了")

    monkeypatch.setattr(scheduler, "run_once", exploding)

    result = await tick()

    assert result.ran is False
    assert "内部炸了" in result.note
    assert "内部炸了" in state().last_error
    # 关键：状态被复位，下一个 tick 还能继续（不是永久卡在 running=True）
    assert state().running is False


async def test_tick_clears_the_error_after_a_good_round(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exploding(**kwargs: Any) -> TickResult:
        raise RuntimeError("第一次失败")

    monkeypatch.setattr(scheduler, "run_once", exploding)
    await tick()
    assert state().last_error

    async def fine(**kwargs: Any) -> TickResult:
        return TickResult(ran=True)

    monkeypatch.setattr(scheduler, "run_once", fine)
    await tick()

    assert state().last_error == ""
    assert state().runs == 1


async def test_tick_skips_when_a_run_is_already_in_progress(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**不能重入**：一轮抓取可能跑几分钟，这期间到了下一个时间点必须跳过。

    同一账号同时开两个浏览器去投递，轻则互相踩页面，重则触发风控。
    """
    state().running = True

    async def should_not_be_called(**kwargs: Any) -> TickResult:
        raise AssertionError("上一轮还在跑时不该再起一轮")

    monkeypatch.setattr(scheduler, "run_once", should_not_be_called)

    result = await tick()

    assert result.ran is False
    assert "上一轮还在执行" in result.note


async def test_tick_resets_running_flag_after_success(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fine(**kwargs: Any) -> TickResult:
        return TickResult(ran=True)

    monkeypatch.setattr(scheduler, "run_once", fine)
    await tick()

    assert state().running is False
    assert state().ticks == 1


async def test_loop_stops_after_max_ticks(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fine(**kwargs: Any) -> TickResult:
        return TickResult()

    monkeypatch.setattr(scheduler, "run_once", fine)

    await scheduler.loop(tick_seconds=0, max_ticks=3)

    assert state().ticks == 3


async def test_start_background_returns_none_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """云端不该挂这个循环：平台按 IP 风控，封号风险不该引给用户。"""
    monkeypatch.setattr(scheduler, "is_local_deployment", lambda: False)

    task = await scheduler.start_background(None)

    assert task is None


async def test_start_background_returns_none_when_disabled_by_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试里靠这个开关关掉循环（否则它会跑在 TestClient 的事件循环里，按真实时间触发）。"""
    monkeypatch.setattr(scheduler, "is_local_deployment", lambda: True)
    monkeypatch.setattr(scheduler.get_settings(), "apply_scheduler_enabled", False, raising=False)

    task = await scheduler.start_background(None)

    assert task is None


async def test_start_background_starts_a_cancellable_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(scheduler, "is_local_deployment", lambda: True)
    # 全局夹具把循环关掉了（APPLY_SCHEDULER_ENABLED=false）；这里显式打开。
    # 必须清缓存——`get_settings` 是 lru_cache 的，直接改环境变量不生效。
    monkeypatch.setenv("APPLY_SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        task = await scheduler.start_background(None, tick_seconds=3600)
        assert task is not None

        await scheduler.stop_background(task)
        assert task.cancelled() or task.done()
    finally:
        get_settings.cache_clear()


async def test_stop_background_tolerates_none() -> None:
    await scheduler.stop_background(None)  # 不抛就是通过
