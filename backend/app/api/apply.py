"""半自动投递 API（M10）：设置、规则、候选、队列、确认、执行、状态。

三条硬约束（PLAN §6.7）在这层就落实：
1. 模块默认关闭，`enabled` 与 `risk_acknowledged` 都满足才允许抓岗位/投递；
2. 投递只发生在 `approved` 的任务上（人工确认闸门，接口层面就没有"直接投"的入口）；
3. 每日/每分钟有硬上限，熔断期间一律不投。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..apply.adapters import BossAdapter, MockPlatformAdapter, PlatformAdapter
from ..apply.adapters.base import ApplyAdapterError, SearchQuery
from ..apply.breaker import is_broken
from ..apply.filters import FilterRule
from ..apply.runner import (
    ApplyDisabledError,
    ApplySettings,
    approve_tasks,
    execute_approved,
    load_settings,
    queue_snapshot,
    save_settings,
    search_and_queue,
    skip_tasks,
)
from ..apply.throttler import (
    MAX_PER_DAY,
    MAX_PER_MINUTE,
    ThrottleConfig,
    ThrottleState,
    remaining_quota,
)
from ..clock import utcnow
from ..config import get_settings
from ..deps import get_llm, get_session
from ..models.apply import ApplyRule, ApplyTask, JobPosting, PlatformAccount
from ..models.core import Resume
from ..providers.llm.base import LLMClient
from ..schemas.apply_job import (
    ApplyRuleIn,
    ApplyRuleOut,
    ApplySearchIn,
    ApplySearchOut,
    ApplySettingsIn,
    ApplySettingsOut,
    ApplyStatusOut,
    ApproveIn,
    CandidateOut,
    ExecuteIn,
    ExecutionItemOut,
    ExecutionOutcomeOut,
    QueueSnapshotOut,
    QueueTaskOut,
    SkipIn,
)

router = APIRouter(prefix="/api/apply", tags=["apply"])

#: 首次使用时自动建的一套规则：都是"你几乎一定会想拦住的东西"
DEFAULT_RULES: tuple[dict[str, object], ...] = (
    {"name": "黑名单公司", "kind": "blacklist", "priority": 10},
    {"name": "已投过去重", "kind": "dedup", "priority": 20},
    {"name": "排除猎头岗", "kind": "headhunter", "priority": 30, "params": {"deny": True}},
    {"name": "HR 需活跃", "kind": "hr_active", "priority": 40, "params": {"require_active": True}},
    {"name": "薪资区间", "kind": "salary", "priority": 50, "params": {"min_salary": 20000, "max_salary": 80000}},
    {
        "name": "屏蔽外包/驻场/培训",
        "kind": "keyword",
        "priority": 60,
        "params": {"deny": ["外包", "驻场", "派遣", "培训费", "包就业"]},
    },
)


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
def _build_adapter(settings: ApplySettings) -> PlatformAdapter:
    if settings.platform == "boss":
        base_dir = get_settings().data_dir / "browser" / "boss"
        return BossAdapter(
            user_data_dir=settings.user_data_dir or str(base_dir),
            selectors=settings.selectors,
        )
    return MockPlatformAdapter()


def _adapter_readiness(settings: ApplySettings) -> tuple[bool, str]:
    if settings.platform != "boss":
        return True, "Mock 适配器：不会真的投递，用来把确认式流程跑通。"

    try:
        import patchright  # noqa: F401
    except ImportError:
        return False, "未安装 patchright：`pip install patchright` 且 `patchright install chrome`。"

    from ..apply.adapters.selectors import load_selectors

    selectors = load_selectors("boss", settings.selectors)
    missing = selectors.missing(
        "search_input",
        "job_card",
        "card_title",
        "greet_button",
        "greet_input",
        "greet_send",
    )
    if missing:
        return False, f"还有 {len(missing)} 个选择器未校准（{missing[0]} 等），见 docs/APPLY_SELECTORS.md。"

    return True, ""


def _ensure_rules(db: Session) -> list[ApplyRule]:
    rows = list(db.exec(select(ApplyRule).order_by(ApplyRule.priority)).all())  # type: ignore[arg-type]
    if rows:
        return rows

    for item in DEFAULT_RULES:
        db.add(ApplyRule(**item))  # type: ignore[arg-type]
    db.commit()
    return list(db.exec(select(ApplyRule).order_by(ApplyRule.priority)).all())  # type: ignore[arg-type]


def _to_filter_rule(row: ApplyRule) -> FilterRule:
    return FilterRule(
        kind=row.kind,
        enabled=row.enabled,
        priority=row.priority,
        params=dict(row.params or {}),
        name=row.name,
    )


def _settings_out(settings: ApplySettings) -> ApplySettingsOut:
    throttle = settings.throttle.clamped()
    return ApplySettingsOut(
        enabled=settings.enabled,
        risk_acknowledged=settings.risk_acknowledged,
        platform=settings.platform,
        default_channel=settings.default_channel,
        want_salary_min=settings.want_salary_min,
        per_minute=throttle.per_minute,
        per_day=throttle.per_day,
        jitter_min_seconds=throttle.jitter_min_seconds,
        jitter_max_seconds=throttle.jitter_max_seconds,
        selectors=settings.selectors,
        user_data_dir=settings.user_data_dir,
        max_per_minute=MAX_PER_MINUTE,
        max_per_day=MAX_PER_DAY,
    )


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
@router.get("/settings", response_model=ApplySettingsOut, summary="投递设置（默认关闭）")
def read_settings(db: Session = Depends(get_session)) -> ApplySettingsOut:
    return _settings_out(load_settings(db))


@router.put("/settings", response_model=ApplySettingsOut, summary="更新投递设置")
def update_settings(payload: ApplySettingsIn, db: Session = Depends(get_session)) -> ApplySettingsOut:
    current = load_settings(db)
    updated = ApplySettings(
        enabled=payload.enabled,
        risk_acknowledged=payload.risk_acknowledged,
        platform=payload.platform or current.platform,
        default_channel=payload.default_channel or current.default_channel,
        want_salary_min=payload.want_salary_min,
        throttle=ThrottleConfig(
            per_minute=payload.per_minute,
            per_day=payload.per_day,
            jitter_min_seconds=payload.jitter_min_seconds,
            jitter_max_seconds=payload.jitter_max_seconds,
        ),
        selectors=dict(payload.selectors or {}),
        user_data_dir=payload.user_data_dir or current.user_data_dir,
    )
    return _settings_out(save_settings(db, updated))


@router.get("/status", response_model=ApplyStatusOut, summary="投递状态（额度、熔断、适配器就绪度）")
def read_status(db: Session = Depends(get_session)) -> ApplyStatusOut:
    settings = load_settings(db)
    account = db.exec(select(PlatformAccount).where(PlatformAccount.platform == settings.platform)).first()
    now = utcnow()

    broken = is_broken(account.circuit_broken_until if account is not None else None, now=now)
    state = ThrottleState(sent_today=account.daily_sent if account is not None else 0)
    ready, note = _adapter_readiness(settings)

    return ApplyStatusOut(
        enabled=settings.enabled,
        risk_acknowledged=settings.risk_acknowledged,
        platform=settings.platform,
        circuit_broken_until=account.circuit_broken_until if account is not None else None,
        broken=broken,
        daily_sent=state.sent_today,
        daily_limit=settings.throttle.clamped().per_day,
        remaining_quota=remaining_quota(state, settings.throttle),
        adapter_ready=ready,
        adapter_note=note,
    )


# --------------------------------------------------------------------------- #
# 规则
# --------------------------------------------------------------------------- #
@router.get("/rules", response_model=list[ApplyRuleOut], summary="过滤规则（首次访问会自动建一套默认规则）")
def read_rules(db: Session = Depends(get_session)) -> list[ApplyRuleOut]:
    return [ApplyRuleOut(**row.model_dump()) for row in _ensure_rules(db)]


@router.post("/rules", response_model=ApplyRuleOut, status_code=201, summary="新增规则")
def create_rule(payload: ApplyRuleIn, db: Session = Depends(get_session)) -> ApplyRuleOut:
    row = ApplyRule(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return ApplyRuleOut(**row.model_dump())


@router.put("/rules/{rule_id}", response_model=ApplyRuleOut, summary="开关或调整规则")
def update_rule(rule_id: int, payload: ApplyRuleIn, db: Session = Depends(get_session)) -> ApplyRuleOut:
    row = db.get(ApplyRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"规则不存在：{rule_id}")

    row.name = payload.name
    row.kind = payload.kind
    row.enabled = payload.enabled
    row.priority = payload.priority
    row.params = dict(payload.params or {})
    db.add(row)
    db.commit()
    db.refresh(row)
    return ApplyRuleOut(**row.model_dump())


@router.delete("/rules/{rule_id}", status_code=204, summary="删除规则")
def delete_rule(rule_id: int, db: Session = Depends(get_session)) -> None:
    row = db.get(ApplyRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"规则不存在：{rule_id}")
    db.delete(row)
    db.commit()


# --------------------------------------------------------------------------- #
# 候选与队列
# --------------------------------------------------------------------------- #
@router.post("/search", response_model=ApplySearchOut, summary="抓岗位 → 过滤 → 打分 → 进待投队列（还没投）")
async def search_jobs(
    payload: ApplySearchIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> ApplySearchOut:
    settings = load_settings(db)
    adapter = _build_adapter(settings)
    rules = [_to_filter_rule(row) for row in _ensure_rules(db)]

    resume_text = ""
    if payload.resume_id is not None:
        resume = db.get(Resume, payload.resume_id)
        if resume is None:
            raise HTTPException(status_code=404, detail=f"简历不存在：{payload.resume_id}")
        resume_text = resume.content_text
    else:
        latest = db.exec(select(Resume).order_by(Resume.id.desc())).first()  # type: ignore[attr-defined]
        resume_text = latest.content_text if latest is not None else ""

    try:
        outcome = await search_and_queue(
            db,
            adapter=adapter,
            query=SearchQuery(
                keywords=list(payload.keywords),
                city=payload.city,
                salary_min=payload.salary_min,
                limit=payload.limit,
            ),
            rules=rules,
            resume_text=resume_text,
            llm=llm,
            settings=settings,
            fetch_detail=payload.fetch_detail,
        )
    except ApplyDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApplyAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await adapter.close()

    return ApplySearchOut(
        fetched=outcome.fetched,
        passed=outcome.passed,
        blocked=outcome.blocked,
        queued=outcome.queued,
        duplicates=outcome.duplicates,
        candidates=[CandidateOut(**asdict(candidate)) for candidate in outcome.candidates],
        notes=outcome.notes,
    )


@router.get("/queue", response_model=list[QueueTaskOut], summary="待投队列")
def read_queue(
    status: str | None = Query(default=None, description="pending | approved | sent | failed | skipped"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[QueueTaskOut]:
    statement = select(ApplyTask)
    if status:
        statement = statement.where(ApplyTask.status == status)
    statement = statement.order_by(ApplyTask.id.desc()).limit(limit)  # type: ignore[attr-defined]

    tasks = list(db.exec(statement).all())
    postings: dict[int, JobPosting] = {}
    ids = [int(task.job_posting_id) for task in tasks if task.job_posting_id]
    if ids:
        for posting in db.exec(select(JobPosting).where(JobPosting.id.in_(ids))).all():  # type: ignore[attr-defined]
            postings[int(posting.id or 0)] = posting

    items: list[QueueTaskOut] = []
    for task in tasks:
        posting = postings.get(int(task.job_posting_id or 0))
        items.append(
            QueueTaskOut(
                id=int(task.id or 0),
                status=task.status,
                company=posting.company if posting is not None else "",
                title=posting.title if posting is not None else "",
                salary_range=posting.salary_range if posting is not None else "",
                city=posting.city if posting is not None else "",
                jd_url=posting.jd_url if posting is not None else "",
                matched_score=posting.matched_score if posting is not None else None,
                say_hi_text=task.say_hi_text,
                attempts=task.attempts,
                error=task.error,
                created_at=task.created_at,
                approved_at=task.approved_at,
                sent_at=task.sent_at,
            )
        )
    return items


@router.get("/queue/snapshot", response_model=QueueSnapshotOut, summary="队列概览")
def read_queue_snapshot(db: Session = Depends(get_session)) -> QueueSnapshotOut:
    return QueueSnapshotOut(**queue_snapshot(db))


@router.post("/queue/approve", summary="人工确认（可选替换招呼语）")
def approve(payload: ApproveIn, db: Session = Depends(get_session)) -> dict[str, int]:
    approved = approve_tasks(db, task_ids=payload.task_ids, say_hi_overrides=payload.say_hi_overrides)
    return {"approved": approved}


@router.post("/queue/skip", summary="跳过若干条")
def skip(payload: SkipIn, db: Session = Depends(get_session)) -> dict[str, int]:
    return {"skipped": skip_tasks(db, task_ids=payload.task_ids)}


@router.post("/execute", response_model=ExecutionOutcomeOut, summary="执行已确认的投递（受限速与熔断约束）")
async def execute(
    payload: ExecuteIn,
    db: Session = Depends(get_session),
) -> ExecutionOutcomeOut:
    settings = load_settings(db)
    adapter = _build_adapter(settings)
    try:
        outcome = await execute_approved(
            db,
            adapter=adapter,
            settings=settings,
            limit=payload.limit,
            dry_run=payload.dry_run,
        )
    except ApplyDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApplyAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await adapter.close()

    return ExecutionOutcomeOut(
        attempted=outcome.attempted,
        sent=outcome.sent,
        failed=outcome.failed,
        stopped_reason=outcome.stopped_reason,
        tripped=outcome.tripped,
        breaker_message=outcome.breaker_message,
        items=[ExecutionItemOut(**asdict(item)) for item in outcome.items],
    )
