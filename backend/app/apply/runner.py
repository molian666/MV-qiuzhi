"""半自动投递编排：抓岗位 → 过滤 → 打分排序 → 待投队列 →（人工确认）→ 限速执行。

**人工确认闸门是硬性设计**（PLAN §6.7）：投递动作只对 `apply_task.status == "approved"` 的记录发生。
即使参数被调得很激进，也不可能出现"无人值守批量投递"——这是这个模块与
get_jobs 那类全自动脚本最本质的区别，也是我们敢把它放进项目的理由。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlmodel import Session, select

from ..clock import utcnow
from ..models.apply import (
    Application,
    ApplicationEvent,
    ApplyTask,
    Blacklist,
    JobPosting,
    PlatformAccount,
)
from ..models.system import AppSetting
from ..providers.llm.base import LLMClient
from .adapters.base import ApplyAdapterError, JobCard, PlatformAdapter, SearchQuery, SendResult
from .breaker import BreakerSignal, circuit_until, describe, is_broken, should_trip
from .filters import FilterRule, JobFacts, evaluate
from .say_hi import build_say_hi_messages, normalize_say_hi
from .scoring import score_job
from .throttler import ThrottleConfig, ThrottleState, can_send, next_delay_seconds, record_send

SETTINGS_KEY = "apply_settings"


# --------------------------------------------------------------------------- #
# 设置（默认关闭 —— 想投必须先显式打开并确认风险）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ApplySettings:
    enabled: bool = False
    risk_acknowledged: bool = False
    platform: str = "mock"
    default_channel: str = "boss"
    want_salary_min: int = 0
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    selectors: dict[str, str] = field(default_factory=dict)
    user_data_dir: str = ""


def load_settings(db: Session) -> ApplySettings:
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}

    throttle_data = data.get("throttle") if isinstance(data.get("throttle"), dict) else {}
    return ApplySettings(
        enabled=bool(data.get("enabled", False)),
        risk_acknowledged=bool(data.get("risk_acknowledged", False)),
        platform=str(data.get("platform") or "mock"),
        default_channel=str(data.get("default_channel") or "boss"),
        want_salary_min=int(data.get("want_salary_min") or 0),
        throttle=ThrottleConfig(
            per_minute=int(throttle_data.get("per_minute") or 3),
            per_day=int(throttle_data.get("per_day") or 30),
            jitter_min_seconds=float(throttle_data.get("jitter_min_seconds") or 3.0),
            jitter_max_seconds=float(throttle_data.get("jitter_max_seconds") or 9.0),
        ).clamped(),
        selectors=dict(data.get("selectors") or {}),
        user_data_dir=str(data.get("user_data_dir") or ""),
    )


def save_settings(db: Session, settings: ApplySettings) -> ApplySettings:
    payload = {
        "enabled": settings.enabled,
        "risk_acknowledged": settings.risk_acknowledged,
        "platform": settings.platform,
        "default_channel": settings.default_channel,
        "want_salary_min": settings.want_salary_min,
        "throttle": {
            "per_minute": settings.throttle.clamped().per_minute,
            "per_day": settings.throttle.clamped().per_day,
            "jitter_min_seconds": settings.throttle.clamped().jitter_min_seconds,
            "jitter_max_seconds": settings.throttle.clamped().jitter_max_seconds,
        },
        "selectors": settings.selectors,
        "user_data_dir": settings.user_data_dir,
    }
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    if row is None:
        row = AppSetting(key=SETTINGS_KEY, value=payload)
    else:
        row.value = payload
        row.updated_at = utcnow()
    db.add(row)
    db.commit()
    return load_settings(db)


class ApplyDisabledError(RuntimeError):
    """模块没开启（或没确认风险）——不允许投递。"""


def ensure_enabled(settings: ApplySettings) -> None:
    if not settings.enabled:
        raise ApplyDisabledError("投递模块当前是关闭的。请在设置页显式开启，并确认你了解账号风险。")
    if not settings.risk_acknowledged:
        raise ApplyDisabledError("还没有勾选风险确认。请先阅读提示并确认后再开启投递。")


# --------------------------------------------------------------------------- #
# 候选生成
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CandidateCard:
    job_posting_id: int
    external_id: str
    company: str
    title: str
    salary_range: str = ""
    city: str = ""
    jd_url: str = ""
    matched_score: float = 0.0
    passed: bool = True
    blocked_by: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    say_hi: str = ""
    task_id: int | None = None


@dataclass(slots=True)
class SearchOutcome:
    fetched: int = 0
    passed: int = 0
    blocked: int = 0
    queued: int = 0
    duplicates: int = 0
    candidates: list[CandidateCard] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _facts_from_card(card: JobCard, *, already_applied: bool, blacklisted: bool) -> JobFacts:
    return JobFacts(
        platform="boss",
        company=card.company,
        title=card.title,
        jd_text=card.jd_text,
        salary_range=card.salary_range,
        city=card.city,
        experience_req=card.experience_req,
        degree_req=card.degree_req,
        company_size=card.company_size,
        company_stage=card.company_stage,
        industry=card.industry,
        hr_name=card.hr_name,
        hr_active=card.hr_active,
        is_headhunter=card.is_headhunter,
        already_applied=already_applied,
        blacklisted=blacklisted,
    )


def _upsert_posting(db: Session, *, platform: str, card: JobCard, verdict_reasons: str, score: float) -> JobPosting:
    existing = db.exec(
        select(JobPosting).where(JobPosting.platform == platform, JobPosting.external_id == card.external_id)
    ).first()

    if existing is None:
        existing = JobPosting(platform=platform, external_id=card.external_id, company=card.company, title=card.title)
        db.add(existing)

    existing.company = card.company or existing.company
    existing.title = card.title or existing.title
    existing.jd_text = card.jd_text or existing.jd_text
    existing.jd_url = card.jd_url or existing.jd_url
    existing.salary_range = card.salary_range or existing.salary_range
    existing.city = card.city or existing.city
    existing.experience_req = card.experience_req or existing.experience_req
    existing.degree_req = card.degree_req or existing.degree_req
    existing.company_size = card.company_size or existing.company_size
    existing.company_stage = card.company_stage or existing.company_stage
    existing.industry = card.industry or existing.industry
    existing.hr_name = card.hr_name or existing.hr_name
    existing.hr_active = card.hr_active
    existing.is_headhunter = card.is_headhunter
    existing.matched_score = score
    existing.filter_verdict = verdict_reasons[:500]
    existing.fetched_at = utcnow()

    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


async def _compose_say_hi(db: Session, *, llm: LLMClient, card: JobCard, resume_text: str) -> str:
    messages = build_say_hi_messages(
        job_title=card.title,
        company=card.company,
        jd_text=card.jd_text,
        resume_text=resume_text,
    )
    try:
        result = await llm.complete(task="say_hi", messages=messages, temperature=0.6)
    except Exception:  # noqa: BLE001 - 招呼语失败不该让整个候选生成失败
        return normalize_say_hi(f"看到{card.title}在招人，我的经历比较对口，想跟您聊聊。")

    return normalize_say_hi(result.text)


async def search_and_queue(
    db: Session,
    *,
    adapter: PlatformAdapter,
    query: SearchQuery,
    rules: Sequence[FilterRule],
    resume_text: str,
    llm: LLMClient,
    settings: ApplySettings,
    fetch_detail: bool = False,
    rng: random.Random | None = None,
) -> SearchOutcome:
    """抓岗位 → 过滤 → 打分 → 生成招呼语 → 进待投队列（status=pending，**还没投**）。"""
    ensure_enabled(settings)
    outcome = SearchOutcome()

    cards = await adapter.search(query)
    outcome.fetched = len(cards)
    if not cards:
        outcome.notes.append("没有抓到岗位。若是 boss 适配器，请先按文档校准选择器。")
        return outcome

    blacklisted_companies = {
        str(row.company).strip().lower()
        for row in db.exec(select(Blacklist)).all()
        if row.company
    }

    for card in cards:
        if fetch_detail:
            try:
                card = await adapter.fetch_detail(card)
            except ApplyAdapterError as exc:
                outcome.notes.append(f"抓详情失败（{card.title}）：{exc}")
                continue

        company_key = (card.company or "").strip().lower()
        already_applied = (
            db.exec(
                select(Application).where(
                    Application.company == card.company,
                    Application.title == card.title,
                )
            ).first()
            is not None
        )
        facts = _facts_from_card(
            card,
            already_applied=already_applied,
            blacklisted=company_key in blacklisted_companies,
        )
        verdict = evaluate(facts, rules)
        score = score_job(facts, resume_text, want_salary_min=settings.want_salary_min)
        posting = _upsert_posting(
            db,
            platform=adapter.name,
            card=card,
            verdict_reasons=verdict.summary,
            score=score,
        )

        candidate = CandidateCard(
            job_posting_id=int(posting.id or 0),
            external_id=card.external_id,
            company=card.company,
            title=card.title,
            salary_range=card.salary_range,
            city=card.city,
            jd_url=card.jd_url,
            matched_score=score,
            passed=verdict.passed,
            blocked_by=list(verdict.blocked_by),
            reasons=list(verdict.reasons),
        )

        if not verdict.passed:
            outcome.blocked += 1
            outcome.candidates.append(candidate)
            continue

        outcome.passed += 1

        existing_task = db.exec(
            select(ApplyTask).where(ApplyTask.job_posting_id == posting.id)
        ).first()
        if existing_task is not None:
            outcome.duplicates += 1
            candidate.task_id = existing_task.id
            candidate.say_hi = existing_task.say_hi_text
            candidate.reasons.append("队列里已有这个岗位（未投或已投）")
            outcome.candidates.append(candidate)
            continue

        say_hi = await _compose_say_hi(db, llm=llm, card=card, resume_text=resume_text)
        candidate.say_hi = say_hi

        task = ApplyTask(
            job_posting_id=posting.id,
            status="pending",
            say_hi_text=say_hi,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        candidate.task_id = task.id
        outcome.queued += 1
        outcome.candidates.append(candidate)

    outcome.candidates.sort(key=lambda item: (-item.matched_score, item.job_posting_id))
    return outcome


# --------------------------------------------------------------------------- #
# 确认与执行
# --------------------------------------------------------------------------- #
def approve_tasks(db: Session, *, task_ids: Sequence[int], say_hi_overrides: dict[int, str] | None = None) -> int:
    """人工确认：把 pending 变成 approved（可选替换招呼语文案）。"""
    overrides = say_hi_overrides or {}
    approved = 0
    for task_id in task_ids:
        task = db.get(ApplyTask, task_id)
        if task is None or task.status != "pending":
            continue
        text = overrides.get(task_id)
        if text is not None and text.strip():
            task.say_hi_text = normalize_say_hi(text)
        task.status = "approved"
        task.approved_at = utcnow()
        db.add(task)
        approved += 1
    db.commit()
    return approved


def skip_tasks(db: Session, *, task_ids: Sequence[int]) -> int:
    skipped = 0
    for task_id in task_ids:
        task = db.get(ApplyTask, task_id)
        if task is None or task.status in {"sent", "skipped"}:
            continue
        task.status = "skipped"
        db.add(task)
        skipped += 1
    db.commit()
    return skipped


@dataclass(slots=True)
class ExecutionItem:
    task_id: int
    company: str
    title: str
    ok: bool
    detail: str = ""


@dataclass(slots=True)
class ExecutionOutcome:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    stopped_reason: str = ""
    tripped: bool = False
    breaker_message: str = ""
    items: list[ExecutionItem] = field(default_factory=list)


def _account(db: Session, platform: str) -> PlatformAccount:
    row = db.exec(select(PlatformAccount).where(PlatformAccount.platform == platform)).first()
    if row is None:
        row = PlatformAccount(platform=platform)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _throttle_state(db: Session, account: PlatformAccount, *, now: datetime) -> ThrottleState:
    state = ThrottleState(sent_today=account.daily_sent)
    # 用最近 1 分钟内已发送的任务还原"本分钟已投几条"（进程重启后仍然准确）
    recent = db.exec(
        select(ApplyTask).where(ApplyTask.status == "sent", ApplyTask.sent_at.is_not(None))  # type: ignore[union-attr]
    ).all()
    cutoff = now - timedelta(minutes=1)
    state.recent_sends = [task.sent_at for task in recent if task.sent_at is not None and task.sent_at > cutoff]
    return state


async def execute_approved(
    db: Session,
    *,
    adapter: PlatformAdapter,
    settings: ApplySettings,
    limit: int = 10,
    rng: random.Random | None = None,
    dry_run: bool = False,
) -> ExecutionOutcome:
    """执行已确认的投递。**只处理 approved**，且受熔断与限速约束。"""
    ensure_enabled(settings)
    outcome = ExecutionOutcome()
    source = rng or random.Random()
    now = utcnow()

    account = _account(db, adapter.name)

    if is_broken(account.circuit_broken_until, now=now):
        outcome.tripped = True
        outcome.stopped_reason = "熔断中"
        outcome.breaker_message = (
            f"账号处于熔断期，恢复时间：{account.circuit_broken_until}。熔断期间不会投递任何岗位。"
        )
        return outcome

    tasks = list(
        db.exec(
            select(ApplyTask)
            .where(ApplyTask.status == "approved")
            .order_by(ApplyTask.approved_at, ApplyTask.id)  # type: ignore[arg-type]
        ).all()
    )
    if not tasks:
        outcome.stopped_reason = "没有已确认待投的岗位"
        return outcome

    config = settings.throttle.clamped()

    for task in tasks[:limit]:
        posting = db.get(JobPosting, task.job_posting_id) if task.job_posting_id else None
        company = posting.company if posting is not None else ""
        title = posting.title if posting is not None else ""
        card = JobCard(
            external_id=posting.external_id if posting is not None else str(task.id),
            company=company,
            title=title,
            jd_url=posting.jd_url if posting is not None else "",
        )

        state = _throttle_state(db, account, now=utcnow())
        decision = can_send(state, config, now=utcnow())
        if not decision.allowed:
            outcome.stopped_reason = decision.reason
            break

        outcome.attempted += 1
        if dry_run:
            outcome.items.append(ExecutionItem(task.id or 0, company, title, True, "演练模式：未真正发送"))
            continue

        result: SendResult = await adapter.send_greeting(card, task.say_hi_text or "")

        if result.ok:
            task.status = "sent"
            task.sent_at = utcnow()
            task.attempts += 1
            db.add(task)

            # 落成一次真实投递记录（source=apply_bot），后续漏斗/归因都能用
            applied_at = task.sent_at
            application = Application(
                job_posting_id=posting.id if posting is not None else None,
                company=company,
                title=title,
                channel=settings.default_channel or adapter.name,
                jd_url=card.jd_url,
                jd_text=(posting.jd_text if posting is not None else ""),
                applied_at=applied_at,
                status="applied",
                salary_range=(posting.salary_range if posting is not None else ""),
                city=(posting.city if posting is not None else ""),
                source="apply_bot",
                external_id=card.external_id,
            )
            db.add(application)
            db.commit()
            db.refresh(application)

            db.add(
                ApplicationEvent(
                    application_id=int(application.id or 0),
                    at=applied_at,
                    from_status="",
                    to_status="applied",
                    raw_ref=f"半自动投递（{adapter.name}）",
                )
            )

            account.daily_sent += 1
            db.add(account)
            db.commit()

            outcome.sent += 1
            outcome.items.append(ExecutionItem(task.id or 0, company, title, True, result.detail))
            await _sleep_between(config, rng=source)
            continue

        # 失败：记状态，并按信号决定是否熔断
        task.status = "failed"
        task.attempts += 1
        task.error = result.detail[:2000]
        db.add(task)
        db.commit()

        outcome.failed += 1
        outcome.items.append(ExecutionItem(task.id or 0, company, title, False, result.detail))

        signal: BreakerSignal | None = result.signal
        if should_trip(signal):
            account.circuit_broken_until = circuit_until(utcnow())
            db.add(account)
            db.commit()
            outcome.tripped = True
            outcome.stopped_reason = "触发熔断，已停止"
            outcome.breaker_message = describe(signal)  # type: ignore[arg-type]
            break

        if signal is not None:
            outcome.stopped_reason = f"遇到适配问题，已停止本轮：{signal.message}"
            break

    return outcome


async def _sleep_between(config: ThrottleConfig, *, rng: random.Random) -> None:
    """投递之间的随机等待（人类化节奏）。测试里把抖动调成 0 就不会拖慢用例。"""
    delay = next_delay_seconds(config, rng=rng)
    if delay > 0:
        await asyncio.sleep(delay)


def queue_snapshot(db: Session) -> dict[str, Any]:
    """队列概览：UI 顶部用它显示"还有多少待确认/已确认"。"""
    rows = db.exec(select(ApplyTask)).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return {
        "total": len(rows),
        "by_status": counts,
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
    }
