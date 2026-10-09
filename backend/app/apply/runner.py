"""半自动投递编排：抓岗位 → 过滤 → 打分排序 → 待投队列 →（人工确认）→ 限速执行。

**默认档位：人工确认闸门**（PLAN §6.7）：投递动作只对 `apply_task.status == "approved"` 的记录发生。
不开启自动模式时，即使参数被调得很激进，也不可能出现"无人值守批量投递"。

**自动模式是可选的**（`apply/capabilities.py` 的 T2/T3 闸门）：用户可以显式开启
全自动投递、多账号轮换、代理与指纹伪装——这些能力**已实现但默认关闭**，且开启要逐级
升级风险确认。无论哪一档，**执行层都是同一段代码**：熔断、限速、失败处理、Application 落库
都在这条路径上，自动模式只是换了"谁来决定 approve"，不绕过任何风控。
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
    ApplyRule,
    ApplyTask,
    Blacklist,
    JobPosting,
    PlatformAccount,
)
from ..models.system import AppSetting
from ..providers.llm.base import LLMClient
from .adapters.base import ApplyAdapterError, JobCard, PlatformAdapter, SearchQuery, SendResult
from .breaker import BreakerSignal, circuit_until, describe, is_broken, should_trip
from .capabilities import CapabilityState, state_from_dict, state_to_dict
from .filters import FilterRule, JobFacts, evaluate
from .pua import has_obfuscated
from .say_hi import build_say_hi_messages, normalize_say_hi
from .scoring import score_job
from .throttler import ThrottleConfig, ThrottleState, can_send, next_delay_seconds, record_send

SETTINGS_KEY = "apply_settings"

#: 首次使用时自动建的一套规则：都是"你几乎一定会想拦住的东西"。
#:
#: 放在 runner 里而不是 API 层：**定时投递也要用同一套规则**（它不经过 HTTP 层）。
#: 两处各写一份的话，"定时跑的时候过滤规则和手动跑不一样"这种 bug 极难发现。
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


def ensure_rules(db: Session) -> list[ApplyRule]:
    """取过滤规则；一条都没有时先建默认的那套（惰性初始化）。"""
    rows = list(db.exec(select(ApplyRule).order_by(ApplyRule.priority)).all())  # type: ignore[arg-type]
    if rows:
        return rows

    for item in DEFAULT_RULES:
        db.add(ApplyRule(**item))  # type: ignore[arg-type]
    db.commit()
    return list(db.exec(select(ApplyRule).order_by(ApplyRule.priority)).all())  # type: ignore[arg-type]


def load_filter_rules(db: Session) -> list[FilterRule]:
    """把库里的规则转成过滤链要的纯数据。"""
    return [
        FilterRule(
            kind=row.kind,
            enabled=row.enabled,
            priority=row.priority,
            params=dict(row.params or {}),
            name=row.name,
        )
        for row in ensure_rules(db)
    ]


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
    #: 搜索用城市代码（Boss 如 101010100=北京）。**必须持久化**：实测不带城市时，
    #: Boss 会用这个 profile 记住的城市——而那个城市可能是之前空 `city=` 参数被跳转时留下的
    #: （我们那次它就记住了"许昌"，之后每次抓取都返回许昌的岗位）。
    city: str = ""
    #: 静默模式：visible（显示窗口）/ offscreen（有头但窗口挪到屏幕外）/ headless（无头）。
    #: **默认 visible**，因为无头最容易被风控识别（实测无头一次就被 Boss 弹进 `请稍候` 循环）。
    window_mode: str = "visible"
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    selectors: dict[str, str] = field(default_factory=dict)
    user_data_dir: str = ""
    #: 分级能力开关（见 `capabilities.py`）。**默认全关**，所以不配置它时
    #: 行为与引入这一层之前完全一致。`risk_acknowledged` 与它内部那份保持同步，
    #: 但读取时取"两者任一为真"，避免旧库/旧前端把用户的确认弄丢。
    capabilities: CapabilityState = field(default_factory=CapabilityState)


def load_settings(db: Session) -> ApplySettings:
    row = db.exec(select(AppSetting).where(AppSetting.key == SETTINGS_KEY)).first()
    data: dict[str, Any] = dict(row.value) if row is not None and isinstance(row.value, dict) else {}

    throttle_data = data.get("throttle") if isinstance(data.get("throttle"), dict) else {}
    legacy_ack = bool(data.get("risk_acknowledged", False))
    capabilities = state_from_dict(data.get("capabilities"))
    # 两个来源取"或"：老库只有顶层的 risk_acknowledged，新配置写进 capabilities 里。
    # 用 or 而不是"新键优先"，是为了不让任何一侧的空值把用户已做过的风险确认抹掉。
    capabilities.risk_acknowledged = capabilities.risk_acknowledged or legacy_ack

    return ApplySettings(
        enabled=bool(data.get("enabled", False)),
        risk_acknowledged=capabilities.risk_acknowledged,
        platform=str(data.get("platform") or "mock"),
        default_channel=str(data.get("default_channel") or "boss"),
        want_salary_min=int(data.get("want_salary_min") or 0),
        city=str(data.get("city") or ""),
        window_mode=str(data.get("window_mode") or "visible"),
        throttle=ThrottleConfig(
            per_minute=int(throttle_data.get("per_minute") or 3),
            per_day=int(throttle_data.get("per_day") or 30),
            jitter_min_seconds=float(throttle_data.get("jitter_min_seconds") or 3.0),
            jitter_max_seconds=float(throttle_data.get("jitter_max_seconds") or 9.0),
        ).clamped(),
        selectors=dict(data.get("selectors") or {}),
        user_data_dir=str(data.get("user_data_dir") or ""),
        capabilities=capabilities,
    )


def save_settings(db: Session, settings: ApplySettings) -> ApplySettings:
    # 顶层 risk_acknowledged 与 capabilities 里那份**始终写同一个值**，
    # 免得出现"列表页说已确认、能力闸门说没确认"这种自相矛盾的状态。
    settings.capabilities.risk_acknowledged = bool(settings.risk_acknowledged)
    payload = {
        "enabled": settings.enabled,
        "risk_acknowledged": settings.risk_acknowledged,
        "platform": settings.platform,
        "default_channel": settings.default_channel,
        "want_salary_min": settings.want_salary_min,
        "city": settings.city,
        "window_mode": settings.window_mode,
        "throttle": {
            "per_minute": settings.throttle.clamped().per_minute,
            "per_day": settings.throttle.clamped().per_day,
            "jitter_min_seconds": settings.throttle.clamped().jitter_min_seconds,
            "jitter_max_seconds": settings.throttle.clamped().jitter_max_seconds,
        },
        "selectors": settings.selectors,
        "user_data_dir": settings.user_data_dir,
        "capabilities": state_to_dict(settings.capabilities),
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


async def _compose_say_hi(db: Session, *, llm: LLMClient, card: JobCard, resume_text: str) -> tuple[str, str]:
    """生成招呼语，返回 `(文案, 错误说明)`。

    以前这里把异常整个吞掉、静静地换成一句模板话——结果是**模型挂了用户也不知道**：
    队列里那两条 09-25 的模板招呼语就是这么来的（用户看到的是"AI 生成"，实际没有）。
    现在把失败原因返回给调用方，由它放进 `SearchOutcome.notes` 显式提示。
    """
    messages = build_say_hi_messages(
        job_title=card.title,
        company=card.company,
        jd_text=card.jd_text,
        resume_text=resume_text,
    )
    try:
        result = await llm.complete(task="say_hi", messages=messages, temperature=0.6)
    except Exception as exc:  # noqa: BLE001 - 招呼语失败不该让整个候选生成失败
        return (
            normalize_say_hi(f"看到{card.title}在招人，我的经历比较对口，想跟您聊聊。"),
            f"{type(exc).__name__}: {exc}",
        )

    text = normalize_say_hi(result.text)
    if not text:
        return (
            normalize_say_hi(f"看到{card.title}在招人，我的经历比较对口，想跟您聊聊。"),
            "模型返回了空文本",
        )
    return text, ""


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
    #: 本轮"本该告诉用户、但以前被静默吃掉"的问题，最后统一汇总进 notes
    say_hi_failures: list[str] = []
    obfuscated_salary_titles: list[str] = []

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
        # 适配器清理过 PUA 码点后就看不出来了，所以同时看标记位
        if getattr(card, "salary_obfuscated", False) or has_obfuscated(card.salary_range):
            obfuscated_salary_titles.append(f"{card.company}·{card.title}")
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

        say_hi, say_hi_error = await _compose_say_hi(db, llm=llm, card=card, resume_text=resume_text)
        if say_hi_error:
            say_hi_failures.append(f"{card.company}·{card.title}（{say_hi_error}）")
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

    # ---- 把"以前会静默发生"的两件事显式报出来 ----
    if obfuscated_salary_titles:
        sample = "、".join(obfuscated_salary_titles[:3])
        outcome.notes.append(
            f"{len(obfuscated_salary_titles)} 个岗位的薪资被平台用自定义字体混淆（如 {sample}），"
            "数字无法解析：**「薪资区间」过滤规则对它们不生效**（不拦，也不误拦）。"
            "要精确按薪资筛，请用平台自己的薪资筛选条件。"
        )
    if say_hi_failures:
        sample = "；".join(say_hi_failures[:3])
        outcome.notes.append(
            f"{len(say_hi_failures)} 条招呼语生成失败，已退化成通用模板（请手动改写后再确认）：{sample}"
        )
    return outcome


# --------------------------------------------------------------------------- #
# 确认与执行
# --------------------------------------------------------------------------- #
def approve_tasks(db: Session, *, task_ids: Sequence[int], say_hi_overrides: dict[int, str] | None = None) -> int:
    """人工确认：把 pending（或**失败的**）变成 approved（可选替换招呼语文案）。

    为什么允许重试 `failed`：投递失败通常是"我们这边的适配问题"（选择器、页面结构、
    浏览器被关掉），跟岗位本身没关系。以前 failed 是死路——唯一的补救是重新抓一次岗位，
    但重抓会撞上"已投过去重"、拿不回原来的确认状态。实测用户就卡在这里：
    一轮失败之后那 8 条只能干看着。
    """
    overrides = say_hi_overrides or {}
    approved = 0
    for task_id in task_ids:
        task = db.get(ApplyTask, task_id)
        if task is None or task.status not in {"pending", "failed"}:
            continue
        text = overrides.get(task_id)
        if text is not None and text.strip():
            task.say_hi_text = normalize_say_hi(text)
        task.status = "approved"
        task.approved_at = utcnow()
        # 重试时把上一次的错误清掉，避免"这一轮成功了但还挂着旧报错"
        task.error = ""
        db.add(task)
        approved += 1
    db.commit()
    return approved


def delete_tasks(db: Session, *, task_ids: Sequence[int]) -> int:
    """把任务从队列里**真正删掉**（不是标成 skipped）。

    为什么除了 `skip_tasks` 还要有它：跳过只是换个状态，队列里那行还在；
    而"这家公司我不想投"往往是想让这一行**彻底消失**。删除只动 `apply_task`，
    不动 `job_posting`（抓到的岗位信息留着），也不动 `application`
    （已投递的历史在投递分析里，删任务不会影响漏斗数据）。
    """
    removed = 0
    for task_id in task_ids:
        task = db.get(ApplyTask, task_id)
        if task is None:
            continue
        db.delete(task)
        removed += 1
    if removed:
        db.commit()
    return removed


def blacklist_companies(db: Session, *, companies: Sequence[str], platform: str = "", reason: str = "manual") -> int:
    """把公司加进黑名单（已有的跳过）。返回新增条数。

    黑名单是"别让它再出现"的正解：`search_and_queue` 会把黑名单公司集合喂给过滤链，
    命中的岗位直接拦下并写明原因。以前只有表没有入口，用户没法自己维护。
    """
    wanted = sorted({(company or "").strip() for company in companies if (company or "").strip()})
    if not wanted:
        return 0

    existing = {
        (str(row.company).strip().lower(), (row.platform or "").strip().lower())
        for row in db.exec(select(Blacklist)).all()
    }
    added = 0
    for company in wanted:
        key = (company.lower(), (platform or "").strip().lower())
        if key in existing:
            continue
        db.add(Blacklist(platform=platform, company=company, reason=reason))
        existing.add(key)
        added += 1
    if added:
        db.commit()
    return added


def blacklist_and_remove(db: Session, *, task_ids: Sequence[int]) -> tuple[int, int]:
    """拉黑这些任务所属的公司，并把它们从队列里移除。返回 `(拉黑数, 移除数)`。

    这两个动作绑在一起是因为它们表达的是同一件事——"这家公司我不想再看到"：
    只拉黑不移除，队列里还留着一堆永远不会被投的僵尸行。
    """
    companies: list[str] = []
    platform = ""
    for task_id in task_ids:
        task = db.get(ApplyTask, task_id)
        if task is None:
            continue
        posting = db.get(JobPosting, task.job_posting_id) if task.job_posting_id else None
        if posting is not None:
            if posting.company:
                companies.append(str(posting.company))
            platform = platform or (posting.platform or "")

    added = blacklist_companies(db, companies=companies, platform=platform)
    removed = delete_tasks(db, task_ids=task_ids)
    return added, removed


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
    #: 本轮要发出去的通知事件。**这里只记录，不发送**。
    #:
    #: 为什么不让 runner 直接发：发送是 HTTP I/O，而这个模块的约定是"短事务、
    #: 不在事务里做慢操作"。把"发生了什么"留成结构化事件，由 API 层在事务结束之后再发，
    #: 顺带让"什么时候该通知"能直接单测（不用起网络）。
    notifications: list[dict[str, Any]] = field(default_factory=list)


def _notify_event(kind: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, **payload}


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
    # 用最近 1 分钟内已发送/已打招呼的任务还原"本分钟已发几条"（进程重启后仍然准确）。
    # M17 起打招呼（greeted）也占限速：它是真实发送动作，不卡住会连发。
    recent = db.exec(
        select(ApplyTask).where(ApplyTask.status.in_(["sent", "greeted"]))  # type: ignore[union-attr]
    ).all()
    cutoff = now - timedelta(minutes=1)
    recent_sends: list[datetime] = []
    for task in recent:
        ts = task.sent_at if task.status == "sent" else task.greeted_at
        if ts is not None and ts > cutoff:
            recent_sends.append(ts)
    state.recent_sends = recent_sends
    return state


async def execute_approved(
    db: Session,
    *,
    adapter: PlatformAdapter,
    settings: ApplySettings,
    limit: int = 10,
    rng: random.Random | None = None,
    dry_run: bool = False,
    action: str = "resume",
) -> ExecutionOutcome:
    """执行已确认的投递。**只处理已确认的记录**，且受熔断与限速约束。

    M17 把"投递"拆成两个动作：
    - `action="greet"`（打招呼）：只发打招呼文字 → `greeted`，不产生投递记录；
    - `action="resume"`（投递简历）：打招呼 + 简历附件 → `sent`，落一次 Application。
    默认 resume 保持 M10 以来的单动作行为（向后兼容）。
    """
    if action not in {"greet", "resume"}:
        raise ValueError(f"未知投递动作：{action}（只支持 greet / resume）")
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

    # 打招呼只处理还没打过招呼的（approved）；投简历处理已确认（approved）与
    # 已打招呼（greeted）——用户可以不打招呼直接投简历，也可以分两阶段走。
    target_statuses = ["approved"] if action == "greet" else ["approved", "greeted"]
    tasks = list(
        db.exec(
            select(ApplyTask)
            .where(ApplyTask.status.in_(target_statuses))  # type: ignore[union-attr]
            .order_by(ApplyTask.approved_at, ApplyTask.id)  # type: ignore[arg-type]
        ).all()
    )
    if not tasks:
        outcome.stopped_reason = "没有已确认待" + ("打招呼" if action == "greet" else "投简历") + "的岗位"
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
        action_label = "打招呼" if action == "greet" else "投递简历"
        #: 投递简历时是否真的带附件：要走 resume_attachment 能力的闸门。
        #: 闸门没过时**仍然发平台的在线简历**（这是 Boss 支持的正常路径），
        #: 但会在结果里写明"发的是在线简历"，不让用户以为附件发出去了。
        want_attachment = action == "resume" and bool(task.resume_image_path)
        attach_on = want_attachment and settings.capabilities.is_on("resume_attachment")
        skipped_attachment_reason = ""
        if want_attachment and not attach_on:
            skipped_attachment_reason = (
                "「投递时附带简历附件」未开启，本次发送的是平台上的在线简历。"
            )
        if dry_run:
            plan = f"演练模式：将{action_label}"
            if action == "resume":
                plan += "并发送" + ("本地简历附件" if attach_on else "平台在线简历")
            outcome.items.append(
                ExecutionItem(task.id or 0, company, title, True, f"{plan}（未真正发送）")
            )
            continue

        try:
            # 打招呼永远是第一步：Boss 必须先建立沟通才能发简历。
            # 注意**不再**把 resume_image 传给 send_greeting——它以前收下这个参数却从没用过，
            # 于是"投递简历"其实只发了招呼语。附件由下面的 send_resume 真正发出去。
            result: SendResult = await adapter.send_greeting(card, task.say_hi_text or "")
        except Exception as exc:  # noqa: BLE001
            # 单个任务抛异常**不能**把整轮执行打成 500（那样剩下的任务会被默默丢掉，
            # 用户只看到一屏 traceback）。这里把它落成"这条失败 + 本轮停在这里"，
            # 剩下的已确认任务保持原状态，下次点对应按钮还能继续。
            detail = f"{type(exc).__name__}: {exc}"
            task.status = "failed"
            task.attempts += 1
            task.error = detail[:1000]
            db.add(task)
            db.commit()
            outcome.failed += 1
            outcome.items.append(ExecutionItem(task.id or 0, company, title, False, detail))
            outcome.stopped_reason = f"发送时出错，已停止本轮：{detail}"
            break

        # ---- 第二步：投递简历时真的把简历发出去 ----
        if result.ok and action == "resume":
            attachment = (task.resume_image_path or None) if attach_on else None
            try:
                resume_result: SendResult = await adapter.send_resume(card, attachment)
            except Exception as exc:  # noqa: BLE001
                resume_result = SendResult(ok=False, detail=f"{type(exc).__name__}: {exc}")

            if not resume_result.ok:
                # **关键**：招呼语发出去了但简历没发出去，绝不能记成"已投递"——
                # 那会让用户以为简历已送达，实际对方只看到一句问候。
                detail = f"招呼语已发送，但简历没有发出去：{resume_result.detail}"
                task.status = "failed"
                task.attempts += 1
                task.error = detail[:2000]
                db.add(task)
                db.commit()
                outcome.failed += 1
                outcome.items.append(ExecutionItem(task.id or 0, company, title, False, detail))
                outcome.notifications.append(
                    _notify_event("failed", company=company, title=title, detail=detail, attempts=task.attempts)
                )
                # 附件路径失效这类问题是我们自己的适配问题，不该熔断当天
                if should_trip(resume_result.signal):
                    account.circuit_broken_until = circuit_until(utcnow())
                    db.add(account)
                    db.commit()
                    outcome.tripped = True
                    outcome.stopped_reason = "触发熔断，已停止"
                    outcome.breaker_message = describe(resume_result.signal)  # type: ignore[arg-type]
                    outcome.notifications.append(
                        _notify_event(
                            "captcha" if resume_result.signal.kind == "captcha" else "circuit",
                            company=company,
                            title=title,
                            detail=resume_result.signal.message,
                            until=str(account.circuit_broken_until),
                        )
                    )
                break

            detail_suffix = resume_result.detail
            if skipped_attachment_reason:
                detail_suffix = f"{detail_suffix}（{skipped_attachment_reason}）"
            result = SendResult(ok=True, detail=f"{result.detail}；{detail_suffix}")

        if result.ok:
            if action == "greet":
                # 打招呼：状态推进到 greeted，等用户决定是否投简历。不建投递记录、
                # 不占每日额度（额度只算"真正投出去的简历"）。
                task.status = "greeted"
                task.greeted_at = utcnow()
                task.attempts += 1
                db.add(task)
                db.commit()
                outcome.sent += 1
                outcome.items.append(ExecutionItem(task.id or 0, company, title, True, result.detail))
                outcome.notifications.append(
                    _notify_event(
                        "sent",
                        company=company,
                        title=title,
                        detail=result.detail,
                        sent_today=account.daily_sent,
                        daily_limit=config.per_day,
                    )
                )
                await _sleep_between(config, rng=source)
                continue

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
            outcome.notifications.append(
                _notify_event(
                    "sent",
                    company=company,
                    title=title,
                    detail=result.detail,
                    sent_today=account.daily_sent,
                    daily_limit=config.per_day,
                )
            )
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
            # 验证码单独走一个事件：那一条是"要你去手动处理"，与"账号被限制了"不同
            outcome.notifications.append(
                _notify_event(
                    "captcha" if signal is not None and signal.kind == "captcha" else "circuit",
                    company=company,
                    title=title,
                    detail=signal.message if signal is not None else "",
                    until=str(account.circuit_broken_until),
                )
            )
            break

        outcome.notifications.append(
            _notify_event("failed", company=company, title=title, detail=result.detail, attempts=task.attempts)
        )
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
        "greeted": counts.get("greeted", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
    }
