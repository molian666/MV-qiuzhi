"""定时投递的后台循环。

**它做什么**：每 30 秒看一次"排期到了吗"，到了就抓岗位（可选连投递一起）并记账。
**它不做什么**：不做常驻服务、不起第二个进程。应用关掉它就不存在了——这是本地优先
项目的定位（用户关机就没有投递，符合预期）。

四条必须处理的现实约束：

1. **不能重入**。一轮抓取可能要跑几分钟；如果这期间到了下一个时间点，必须跳过而不是
   并行再起一轮——同一个账号同时开两个浏览器去投递，轻则互相踩页面，重则触发风控。
   `_running` 标记 + "一天只跑一次"的日期判定一起保证这件事。
2. **循环绝不能因为一次异常而死掉**。任何异常都记下来、继续等下一个时间点。
   一个后台任务静默死掉是这类功能最难查的故障：用户以为在自动跑，其实早就停了。
3. **闸门在真正动手之前校验，而不是在保存配置时**。用户可能在设完排期之后把自动投递
   关掉了；那时必须**降级成只入队**并留下说明，而不是照旧投出去。
4. **浏览器适配器用完就关**。它自己是单例式的重资源（持久化 Chrome 上下文），
   留着不关会占住 profile 目录，用户下次手动抓取会撞上"目录被占用"。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from sqlmodel import select

from ..clock import utcnow
from ..config import get_settings
from ..db import session_scope
from ..deploy import is_local_deployment
from ..models.core import Resume
from .adapters.base import SearchQuery
from .autopilot import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_MAX_PER_RUN,
    Candidate,
    select_auto_approvals,
)
from .runner import (
    ApplySettings,
    approve_tasks,
    execute_approved,
    load_filter_rules,
    load_settings,
    search_and_queue,
)
from .schedule import due_run_at
from .schedule_store import MODE_APPLY, MODE_QUEUE, ScheduleSettings, load_schedule, local_now, mark_run

logger = logging.getLogger(__name__)

#: 轮询间隔。30 秒对"每天几点"这种精度足够，又不至于空转太频繁。
TICK_SECONDS = 30.0


@dataclass(slots=True)
class TickResult:
    """一次轮询的结果。`ran` 表示这一轮真的执行了投递流程。"""

    ran: bool = False
    due_at: datetime | None = None
    mode: str = ""
    queued: int = 0
    approved: int = 0
    sent: int = 0
    note: str = ""


@dataclass(slots=True)
class SchedulerState:
    """进程内的调度状态。只用于"防重入"和"上一轮出了什么错"。"""

    running: bool = False
    last_error: str = ""
    last_tick_at: datetime | None = None
    ticks: int = 0
    runs: int = 0


_state = SchedulerState()


def state() -> SchedulerState:
    return _state


def reset_state() -> None:
    """测试用：清掉进程内状态。"""
    global _state
    _state = SchedulerState()


# --------------------------------------------------------------------------- #
# 单轮执行
# --------------------------------------------------------------------------- #
def _daily_remaining(db: Any, settings: ApplySettings) -> int:
    """自动模式今天还能投几条。

    自动模式的额度**独立且更低**：无人值守时人不在旁边，出问题没人踩刹车。
    手动额度（`throttle.per_day`）仍然由 `execute_approved` 的限速器把关，
    这里只是一道额外的、更紧的闸。
    """
    return min(DEFAULT_DAILY_LIMIT, max(0, settings.throttle.clamped().per_day))


async def run_once(
    *,
    now: datetime | None = None,
    adapter_factory: Callable[[ApplySettings], Any] | None = None,
    llm: Any = None,
) -> TickResult:
    """看一次排期；该跑就跑。**这是唯一有副作用的地方**，其余都是纯函数。

    `adapter_factory` 与 `llm` 可注入（测试用假适配器，不需要真浏览器）。
    """
    moment = now or local_now()

    with session_scope() as db:
        schedule = load_schedule(db)
        settings = load_settings(db)
        due_at = _due_at(schedule, moment)
        if due_at is None:
            return TickResult()

        # 闸门在这里校验：用户可能在设完排期之后把自动投递关掉了
        mode = schedule.mode
        auto_ok = settings.capabilities.is_on("auto_apply") and settings.capabilities.is_on("scheduled_apply")
        downgraded = ""
        if mode == MODE_APPLY and not auto_ok:
            mode = "queue"
            downgraded = "自动投递能力已关闭，本次降级为只入队（没有投递任何岗位）。"

        plan = _RunPlan(
            mode=mode,
            limit=schedule.limit,
            keywords=list(schedule.keywords),
            city=schedule.city,
            fetch_detail=schedule.fetch_detail,
            downgraded=downgraded,
            daily_remaining=_daily_remaining(db, settings),
        )

    if adapter_factory is None or llm is None:
        # 延迟导入：这两个依赖比较重（浏览器/HTTP 客户端），不该在 import 期就建
        adapter_factory, llm = _default_dependencies(adapter_factory, llm)

    return await _execute_plan(plan, due_at=due_at, moment=moment, adapter_factory=adapter_factory, llm=llm)


@dataclass(slots=True)
class _RunPlan:
    mode: str
    limit: int
    keywords: list[str]
    city: str
    fetch_detail: bool
    downgraded: str = ""
    daily_remaining: int = 0
    notes: list[str] = field(default_factory=list)


def _due_at(schedule: ScheduleSettings, moment: datetime) -> datetime | None:
    return due_run_at(
        schedule.schedule,
        last_run_date=schedule.last_run_date,
        now=moment,
        grace_minutes=schedule.grace_minutes,
    )


def _default_dependencies(adapter_factory: Any, llm: Any) -> tuple[Any, Any]:
    from ..deps import build_llm_client

    def _factory(settings: ApplySettings) -> Any:
        # 与 `api/apply.py` 同一个装配路径，避免"定时跑用 mock、手动跑用真适配器"
        from ..api.apply import _build_adapter

        return _build_adapter(settings)

    return (adapter_factory or _factory, llm or build_llm_client())


async def _execute_plan(
    plan: _RunPlan,
    *,
    due_at: datetime,
    moment: datetime,
    adapter_factory: Callable[[ApplySettings], Any],
    llm: Any,
) -> TickResult:
    result = TickResult(ran=True, due_at=due_at, mode=plan.mode)
    notes = list(plan.notes)
    if plan.downgraded:
        notes.append(plan.downgraded)

    with session_scope() as db:
        settings = load_settings(db)
        rules = load_filter_rules(db)
        resume_text = ""
        # 用简历正文打分：没有简历时 score_job 会退回线性分，不会崩
        resume = db.exec(select(Resume).order_by(Resume.version.desc())).first()  # type: ignore[arg-type]
        if resume is not None:
            resume_text = resume.content_text or ""

    adapter = adapter_factory(settings)
    try:
        with session_scope() as db:
            outcome = await search_and_queue(
                db,
                adapter=adapter,
                query=SearchQuery(
                    keywords=list(plan.keywords),
                    city=plan.city,
                    limit=plan.limit,
                ),
                rules=rules,
                resume_text=resume_text,
                llm=llm,
                settings=settings,
                fetch_detail=plan.fetch_detail,
            )
        result.queued = outcome.queued
        notes.extend(outcome.notes)

        if plan.mode == MODE_APPLY:
            approved, send_note = await _auto_approve_and_send(
                outcome=outcome,
                settings=settings,
                adapter=adapter,
                llm=llm,
                limit=plan.limit,
                daily_remaining=plan.daily_remaining,
            )
            result.approved = approved
            if send_note:
                notes.append(send_note)
    finally:
        # 浏览器是重资源，用完必须关：留着会占住 profile 目录，
        # 用户下次手动抓取会撞上"目录被另一个 Chrome 占用"
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001 - 关不掉不该影响记账
            logger.exception("关闭定时投递的浏览器适配器失败")

    result.note = "；".join(note for note in notes if note)[:500]
    with session_scope() as db:
        mark_run(db, at=utcnow(), result=result.note or f"入队 {result.queued} 条")
    return result


async def _auto_approve_and_send(
    *,
    outcome: Any,
    settings: ApplySettings,
    adapter: Any,
    llm: Any,
    limit: int,
    daily_remaining: int,
) -> tuple[int, str]:
    """自动通过闸门并投递。返回 (通过条数, 说明)。"""
    candidates = [
        Candidate(
            task_id=item.task_id,
            matched_score=item.matched_score,
            passed=item.passed,
        )
        for item in outcome.candidates
        if item.task_id is not None
    ]
    decision = select_auto_approvals(
        candidates,
        max_per_run=min(DEFAULT_MAX_PER_RUN, limit),
        daily_remaining=daily_remaining,
    )
    if not decision.approved:
        return 0, "自动通过：本次没有达到阈值的岗位（没有投递）"

    with session_scope() as db:
        # 把理由写进任务，用户能在队列里看到"它凭什么被自动投了"
        approve_tasks(db, task_ids=[item.task_id for item in decision.approved])
        notes = "；".join(item.reason for item in decision.approved[:3])

    with session_scope() as db:
        executed = await execute_approved(
            db,
            adapter=adapter,
            settings=settings,
            limit=len(decision.approved),
            action="resume",
        )

    return len(decision.approved), f"自动投递 {executed.sent} 条（{notes}）"


# --------------------------------------------------------------------------- #
# 循环
# --------------------------------------------------------------------------- #
async def loop(*, tick_seconds: float = TICK_SECONDS, max_ticks: int | None = None) -> None:
    """后台循环。`max_ticks` 只给测试用（跑几轮就退出）。"""
    ticks = 0
    while True:
        await tick()
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return
        await asyncio.sleep(tick_seconds)


async def tick() -> TickResult:
    """轮询一次。**任何异常都被吞掉并记录**——循环不能因为一次失败而死。"""
    _state.last_tick_at = utcnow()
    _state.ticks += 1

    if _state.running:
        # 上一轮还在跑（抓取可能要好几分钟）。**不能并行再起一轮**：
        # 同一账号同时开两个浏览器投递，轻则互相踩页面，重则触发风控。
        return TickResult(note="上一轮还在执行，本次跳过")

    _state.running = True
    try:
        result = await run_once()
        if result.ran:
            _state.runs += 1
        _state.last_error = ""
        return result
    except Exception as exc:  # noqa: BLE001
        _state.last_error = f"{type(exc).__name__}: {exc}"
        logger.exception("定时投递轮询失败，已忽略并继续等待下一个时间点")
        return TickResult(note=f"执行失败：{_state.last_error}")
    finally:
        _state.running = False


async def start_background(app: Any, *, tick_seconds: float = TICK_SECONDS) -> asyncio.Task | None:
    """在应用启动时挂上循环，返回那个 task（关停时取消它）。

    只在**本机运行**时挂：定时投递会驱动浏览器去投递，云端 IP 的封号风险不该由用户承担
    （与 `deploy.require_local_run` 同一个理由）。
    """
    if not is_local_deployment():
        logger.info("非本机运行，不启动定时投递循环")
        return None
    if not get_settings().apply_scheduler_enabled:
        # 测试里关掉：循环跑在 TestClient 自己的事件循环里，"到没到点"会取决于真实时间，
        # 让相关断言变得不确定（见 config.apply_scheduler_enabled 的说明）
        logger.info("定时投递循环被配置关闭（APPLY_SCHEDULER_ENABLED=false）")
        return None

    task = asyncio.create_task(loop(tick_seconds=tick_seconds), name="apply-scheduler")
    return task


async def stop_background(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
