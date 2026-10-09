"""半自动投递 API（M10）：设置、规则、候选、队列、确认、执行、状态。

三条硬约束（PLAN §6.7）在这层就落实：
1. 模块默认关闭，`enabled` 与 `risk_acknowledged` 都满足才允许抓岗位/投递；
2. 投递只发生在 `approved` 的任务上（人工确认闸门，接口层面就没有"直接投"的入口）；
3. 每日/每分钟有硬上限，熔断期间一律不投。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlmodel import Session, select

from ..apply.adapters import PLATFORM_LABELS, PLATFORMS, MockPlatformAdapter, PlatformAdapter
from ..apply.adapters.base import ApplyAdapterError, SearchQuery
from ..apply.adapters.browser import WINDOW_MODES, is_headless_mode, window_args
from ..apply.attachment import AttachmentError, store_attachment
from ..apply.breaker import is_broken
from ..apply.capabilities import (
    CAPABILITIES,
    CAPTCHA_MODES,
    TIER_LABELS,
    TIER_SUMMARIES,
    CapabilityState,
    capability,
    enable,
    gate,
)
from ..apply.cities import city_code
from ..apply.filters import FilterRule
from ..apply.notify import (
    CHANNELS,
    CHANNEL_LABELS,
    NotifySettings,
    load_notify_settings,
    notify as send_notify,
    save_notify_settings,
)
from ..apply.profile_store import (
    DEFAULT_PROFILE_DAILY_LIMIT,
    ProfileChoiceResult,
    ProfileMeta,
    bump_profile_usage,
    choose_profile,
    create_profile,
    list_profiles,
    load_meta,
    mark_profile_used,
    profile_usage_today,
    save_meta,
    set_profile_meta,
)
from ..apply.proxy import Fingerprint, parse_proxy, probe_proxy, proxy_is_usable
from ..apply.schedule import MAX_SLOTS_PER_DAY, format_hhmm
from ..apply.schedule_store import (
    MODE_APPLY,
    MODE_LABELS,
    MODE_QUEUE,
    ScheduleSettings,
    load_schedule,
    preview,
    save_schedule,
)
from ..apply.runner import (
    ApplyDisabledError,
    ApplySettings,
    approve_tasks,
    blacklist_and_remove,
    delete_tasks,
    ensure_rules,
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
from ..apply_service import autofill_blacklist
from ..clock import utcnow
from ..config import get_settings
from ..deploy import require_local_run
from ..deps import get_llm, get_session
from ..models.apply import ApplyRule, ApplyTask, JobPosting, PlatformAccount, PlatformProfile
from ..models.core import Resume
from ..providers.llm.base import LLMClient
from ..schemas.apply_job import (
    ApplyCapabilitiesOut,
    ApplyNotifySettingsIn,
    ApplyNotifySettingsOut,
    ApplyNotifyTestIn,
    ApplyProfileIn,
    ApplyProfileOut,
    ApplyProfilesOut,
    ApplyProxyProbeIn,
    ApplyRuleIn,
    ApplyRuleOut,
    ApplyScheduleIn,
    ApplyScheduleOut,
    ApplySearchIn,
    ApplySearchOut,
    ApplySettingsIn,
    ApplySettingsOut,
    ApplyStatusOut,
    ApproveIn,
    CandidateOut,
    CapabilityOut,
    ExecuteIn,
    ExecutionItemOut,
    ExecutionOutcomeOut,
    QueueSnapshotOut,
    QueueTaskOut,
    SkipIn,
)

router = APIRouter(prefix="/api/apply", tags=["apply"])
logger = logging.getLogger(__name__)

#: 验证码策略的**用户可读说明**。取值必须与 `capabilities.CAPTCHA_MODES` 一致，
#: 这里的 filter 就是保证这一点——加了新策略却忘了写说明，它不会悄悄出现在界面上。
#: 注意**没有"自动绕过"这一项**，这是有意的（见 capabilities.py 的说明）。
CAPTCHA_MODE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "key": "circuit",
        "label": "熔断停投当天（默认）",
        "detail": "检测到验证码或滑块就停止今天的投递，等你自己处理完再手动恢复。最保守。",
    },
    {
        "key": "wait",
        "label": "暂停并等我手动完成",
        "detail": "暂停程序并通知你；你在浏览器里过掉验证码后，程序从当前这条继续。",
    },
)

#: 默认规则与"取规则"的实现在 `apply/runner.py`（定时投递也要用同一套，见 `ensure_rules`）。




# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
def _build_adapter(
    settings: ApplySettings,
    *,
    user_data_dir: str = "",
    proxy: str = "",
    fingerprint: Fingerprint | None = None,
) -> PlatformAdapter:
    """按配置里的平台造适配器。

    平台注册表在 `apply/adapters/__init__.py`，所以接新平台只要在那边加一行——
    这里不需要知道有哪些平台。

    `user_data_dir` / `proxy` / `fingerprint` 由多账号选号的结果传进来；不传就是
    单账号模式（用平台默认的浏览器目录、直连、交给 patchright 自己的默认特征）。
    """
    factory = PLATFORMS.get(settings.platform)
    if factory is None or factory is MockPlatformAdapter:
        return MockPlatformAdapter()

    base_dir = get_settings().data_dir / "browser" / settings.platform
    # `user_data_dir` 是相对数据目录存的（换机器不会失效），这里再拼回来
    chosen_dir = user_data_dir or settings.user_data_dir
    if chosen_dir:
        candidate = Path(chosen_dir)
        resolved = candidate if candidate.is_absolute() else get_settings().data_dir / candidate
    else:
        resolved = base_dir

    return factory(  # type: ignore[return-value]
        user_data_dir=str(resolved),
        selectors=settings.selectors,
        # 静默模式：有头/屏幕外/无头（见 browser.window_args 的说明——无头最容易被风控识别）
        headless=is_headless_mode(settings.window_mode),
        extra_args=window_args(settings.window_mode),
        proxy=proxy,
        fingerprint=fingerprint,
    )


def _adapter_readiness(settings: ApplySettings) -> tuple[bool, str]:
    if settings.platform == "mock":
        return True, "Mock 适配器：不会真的投递，用来把确认式流程跑通。"

    try:
        import patchright  # noqa: F401
    except ImportError:
        return False, "未安装 patchright：`pip install patchright` 且 `patchright install chrome`。"

    from ..apply.adapters.selectors import load_selectors

    label = PLATFORM_LABELS.get(settings.platform, settings.platform)
    selectors = load_selectors(settings.platform, settings.selectors)
    missing = selectors.missing(
        "search_input",
        "job_card",
        "card_title",
        "greet_button",
        "greet_input",
        "greet_send",
    )
    if missing:
        return False, f"{label}还有 {len(missing)} 个选择器未校准（{missing[0]} 等），见 docs/APPLY_SELECTORS.md。"

    return True, ""


def _ensure_rules(db: Session) -> list[ApplyRule]:
    """转发到 `apply.runner.ensure_rules`。

    规则与默认值现在只有**一份**（在 runner 里）：定时投递不经过 HTTP 层，
    两处各写一份的话，"定时跑的时候过滤规则和手动跑不一样"这种 bug 极难发现。
    """
    return ensure_rules(db)


def _to_filter_rule(row: ApplyRule) -> FilterRule:
    return FilterRule(
        kind=row.kind,
        enabled=row.enabled,
        priority=row.priority,
        params=dict(row.params or {}),
        name=row.name,
    )


def _sync_salary_rule(db: Session, settings: ApplySettings) -> None:
    """把「期望月薪下限」写进「薪资区间」规则里。

    **为什么必须同步**：界面上这两个东西看起来是同一件事，实际一个是**评分**用的
    （`want_salary_min` → `score_job`），另一个才是**真正拦截**用的（筛选规则的 `min_salary`）。
    实测用户把下限填成 8000，规则里还是建库时的默认 `20000-80000`，于是 10-15K 的岗位照样被拦，
    拦截理由还写着"与期望 20000-80000 无交集"——用户完全无从理解。
    下限为 0 视为"没意见"，此时不动那条规则（保留它自己的默认值或用户手改的值）。
    """
    if settings.want_salary_min <= 0:
        return
    # 先确保默认规则已建立：规则是惰性创建的（第一次读 /rules 才建），
    # 如果只在"规则已存在"时同步，那么"先改设置、后打开规则页"就会静默不同步。
    _ensure_rules(db)
    rule = db.exec(select(ApplyRule).where(ApplyRule.kind == "salary")).first()
    if rule is None:
        return
    params = dict(rule.params or {})
    if int(params.get("min_salary") or 0) == settings.want_salary_min:
        return
    params["min_salary"] = settings.want_salary_min
    rule.params = params
    db.add(rule)
    db.commit()


def _merge_capabilities(current: CapabilityState, payload: Any) -> CapabilityState:
    """把请求体里的能力选择合并进当前状态。

    四条规则：

    1. **不传 `capabilities` 就完全不动能力状态**。老前端不知道这个字段，它的 PUT 不该
       把用户已做的选择清掉。
    2. **确认短语只增不减**。用户已经打过的那句话不会被后来的请求清掉——否则任何一次
       普通的设置保存都会把 T2/T3 的确认抹掉，用户会莫名其妙地"自动投递自己关了"。
    3. **整份覆盖 `enabled`**：请求里没提到 = 用户取消勾选（前端就是整份回传的）。
    4. **依赖闭包，与遍历顺序无关**。这里是先算"要留哪些"，而不是逐个 `enable()`：
       逐个 enable 时，"先关 auto_apply 触发的级联"会被"紧接着又把 scheduled_apply 打开"
       覆盖掉（顺序一变结果就变）。用闭包算出来就没有这个隐患。
    """
    state = current
    if payload is None:
        return state

    for phrase in payload.signed_phrases or []:
        text = str(phrase).strip()
        if text and text not in state.signed_phrases:
            state.signed_phrases = (*state.signed_phrases, text)

    wanted = payload.enabled or {}
    # 只保留"自己勾了、且所有依赖也勾了"的能力。按注册表遍历（不是遍历请求体的键），
    # 所以请求体里的垃圾键根本读不到；又因为依赖项必须在注册表里排在被依赖者之前，
    # 一趟扫描就能收敛。
    keep: dict[str, bool] = {}
    for item in CAPABILITIES:
        if not wanted.get(item.key, False):
            continue
        if all(keep.get(dependency, False) for dependency in item.requires):
            keep[item.key] = True

    # 清掉没被保留的（`enable(..., on=False)` 会顺带级联，
    # 所以不会留下"定时开着、自动没开"这种矛盾状态）
    for item in CAPABILITIES:
        if not keep.get(item.key, False) and state.wants(item.key):
            enable(state, item.key, on=False)

    for item in CAPABILITIES:
        if keep.get(item.key, False) and not state.wants(item.key):
            enable(state, item.key, on=True)

    state.run_local = bool(payload.run_local)
    state.captcha_mode = payload.captcha_mode or state.captcha_mode
    return state


def _capability_item(state: CapabilityState, key: str) -> CapabilityOut:
    item = capability(key)
    result = gate(key, state)
    return CapabilityOut(
        key=item.key,
        label=item.label,
        tier=int(item.tier),
        tier_label=TIER_LABELS.get(item.tier, ""),
        tier_summary=TIER_SUMMARIES.get(item.tier, ""),
        summary=item.summary,
        default=item.default,
        requires=list(item.requires),
        wanted=state.wants(key),
        on=result.allowed,
        # 用户没勾的时候不报"未开启"以外的原因——那只是噪音
        blocked_reason="" if result.allowed else result.reason,
        missing_phrase=result.missing_phrase,
    )


def _capabilities_out(state: CapabilityState) -> ApplyCapabilitiesOut:
    items = [_capability_item(state, item.key) for item in CAPABILITIES]
    return ApplyCapabilitiesOut(
        items=items,
        risk_acknowledged=state.risk_acknowledged,
        signed_phrases=list(state.signed_phrases),
        run_local=state.run_local,
        captcha_mode=state.captcha_mode,
        captcha_modes=[dict(item) for item in CAPTCHA_MODE_OPTIONS if item["key"] in CAPTCHA_MODES],
        tier_labels={str(int(tier)): label for tier, label in TIER_LABELS.items()},
        tier_summaries={str(int(tier)): text for tier, text in TIER_SUMMARIES.items()},
        any_auto_enabled=any(item.on and item.tier >= 2 for item in items),
    )


def _mask_webhook(url: str) -> str:
    """把 webhook 地址脱敏后再给前端。

    **不能回显完整地址**：企业微信机器人的 URL 里那个 `key=` 就是凭据，
    拿到它任何人都能往用户群里发消息。所以只留"域名 + 末尾 4 位"，
    够用户确认自己填对了哪一个，又不足以被拿来用。
    """
    text = (url or "").strip()
    if not text:
        return ""
    tail = text[-4:] if len(text) > 4 else ""
    head = text.split("://", 1)[-1].split("/", 1)[0]
    return f"{head}/…{tail}" if head else f"…{tail}"


def _notify_out(settings: NotifySettings) -> ApplyNotifySettingsOut:
    return ApplyNotifySettingsOut(
        # 地址一律不回显（webhook URL 里的 key 等同于凭据）。
        # 前端要判断"填没填"看 configured，要显示用 masked_url。
        url="",
        channel=settings.channel,
        on_sent=settings.on_sent,
        on_circuit=settings.on_circuit,
        on_captcha=settings.on_captcha,
        on_failed=settings.on_failed,
        configured=settings.configured,
        masked_url=_mask_webhook(settings.url),
        channels=[{"key": key, "label": CHANNEL_LABELS.get(key, key)} for key in CHANNELS],
    )


def _schedule_out(db: Session, settings: ScheduleSettings) -> ApplyScheduleOut:
    info = preview(db)
    return ApplyScheduleOut(
        enabled=settings.enabled,
        times=list(settings.times),
        # 解析后的时间点单独给：界面能看出"我填了 5 个但实际只生效 3 个"
        parsed_times=[format_hhmm(moment) for moment in settings.parsed_times],
        mode=settings.mode,
        limit=settings.limit,
        keywords=list(settings.keywords),
        city=settings.city,
        fetch_detail=settings.fetch_detail,
        grace_minutes=settings.grace_minutes,
        description=info.description,
        active=info.active,
        next_run_at=info.next_run_at,
        due_now=info.due_now,
        last_run_at=info.last_run_at,
        last_result=info.last_result,
        mode_labels=dict(MODE_LABELS),
        max_slots=MAX_SLOTS_PER_DAY,
    )


def _settings_out(settings: ApplySettings) -> ApplySettingsOut:
    throttle = settings.throttle.clamped()
    return ApplySettingsOut(
        enabled=settings.enabled,
        risk_acknowledged=settings.risk_acknowledged,
        platform=settings.platform,
        default_channel=settings.default_channel,
        want_salary_min=settings.want_salary_min,
        city=settings.city,
        window_mode=settings.window_mode,
        per_minute=throttle.per_minute,
        per_day=throttle.per_day,
        jitter_min_seconds=throttle.jitter_min_seconds,
        jitter_max_seconds=throttle.jitter_max_seconds,
        selectors=settings.selectors,
        user_data_dir=settings.user_data_dir,
        max_per_minute=MAX_PER_MINUTE,
        max_per_day=MAX_PER_DAY,
        capabilities=_capabilities_out(settings.capabilities),
    )


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
@router.get(
    "/capabilities",
    response_model=ApplyCapabilitiesOut,
    summary="投递能力开关（分级；默认全关）",
)
def read_capabilities(db: Session = Depends(get_session)) -> ApplyCapabilitiesOut:
    """单独一个只读入口，方便前端在设置页之外（比如状态条）显示"自动投递是否在跑"。

    写入口是 `PUT /api/apply/settings` 的 `capabilities` 字段——**不另开一个写接口**，
    免得出现"两个地方都能改开关、谁后写谁生效"的问题。
    """
    return _capabilities_out(load_settings(db).capabilities)


@router.get("/settings", response_model=ApplySettingsOut, summary="投递设置（默认关闭）")
def read_settings(db: Session = Depends(get_session)) -> ApplySettingsOut:
    return _settings_out(load_settings(db))


@router.put("/settings", response_model=ApplySettingsOut, summary="更新投递设置")
def update_settings(payload: ApplySettingsIn, db: Session = Depends(get_session)) -> ApplySettingsOut:
    current = load_settings(db)
    try:
        city = city_code(payload.city)
    except ValueError as exc:
        # 宁可让用户改一个字，也不要静默搜错城市（Boss 对非法城市是"换一个城市继续搜"）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = ApplySettings(
        enabled=payload.enabled,
        risk_acknowledged=payload.risk_acknowledged,
        platform=payload.platform or current.platform,
        default_channel=payload.default_channel or current.default_channel,
        want_salary_min=payload.want_salary_min,
        # 注意：**不能用 `or current.city`**——空城市是有意义的状态（"用平台当前城市"），
        # 用 or 兜底会导致城市一旦设上就再也清不掉。这里跟 enabled/want_salary_min 一样按请求体重置
        # （前端与校准脚本都是整份回传，不会误清）。
        city=city,
        # 静默模式：非法值回退到当前值，避免写进一个前端不认识的模式
        window_mode=payload.window_mode if payload.window_mode in WINDOW_MODES else current.window_mode,
        throttle=ThrottleConfig(
            per_minute=payload.per_minute,
            per_day=payload.per_day,
            jitter_min_seconds=payload.jitter_min_seconds,
            jitter_max_seconds=payload.jitter_max_seconds,
        ),
        selectors=dict(payload.selectors or {}),
        user_data_dir=payload.user_data_dir or current.user_data_dir,
        # 能力开关：合并进**当前**状态，而不是从零重建——已签的确认短语和
        # 请求里没提到的高风险能力不能被一次普通保存重置。
        capabilities=_merge_capabilities(current.capabilities, payload.capabilities),
    )
    saved = save_settings(db, updated)
    # 让「期望月薪下限」真正生效：同步进「薪资区间」规则（否则那个输入框只管打分、不管拦截）
    _sync_salary_rule(db, saved)
    return _settings_out(saved)


@router.get(
    "/notify/settings",
    response_model=ApplyNotifySettingsOut,
    summary="通知配置（企业微信 / 通用 Webhook）",
)
def read_notify_settings(db: Session = Depends(get_session)) -> ApplyNotifySettingsOut:
    return _notify_out(load_notify_settings(db))


@router.put(
    "/notify/settings",
    response_model=ApplyNotifySettingsOut,
    summary="更新通知配置",
)
def update_notify_settings(
    payload: ApplyNotifySettingsIn, db: Session = Depends(get_session)
) -> ApplyNotifySettingsOut:
    # 通知属于 T1 档（会对外发消息），要先勾风险确认才能开
    settings = load_settings(db)
    if not settings.capabilities.risk_acknowledged:
        raise HTTPException(
            status_code=403,
            detail="通知属于「对外发消息」这一档，请先在能力开关里勾选风险确认。",
        )

    channel = payload.channel if payload.channel in CHANNELS else "wechat"
    saved = save_notify_settings(
        db,
        NotifySettings(
            url=payload.url,
            channel=channel,
            on_sent=payload.on_sent,
            on_circuit=payload.on_circuit,
            on_captcha=payload.on_captcha,
            on_failed=payload.on_failed,
        ),
    )
    return _notify_out(saved)


@router.post("/notify/test", summary="发一条测试通知（永远返回 200，失败原因在 detail 里）")
async def test_notify_settings(
    payload: ApplyNotifyTestIn, db: Session = Depends(get_session)
) -> dict[str, Any]:
    """测试连通性。

    **永远返回 200**，与 `/api/system/test-llm` 同一个理由：测试失败是正常结果
    而不是接口错误，用 4xx/5xx 表达会让前端只拿到一句笼统的报错，而这里最需要的
    正是**原始错误**（URL 写错与机器人被停用是完全不同的修法）。
    """
    stored = load_notify_settings(db)
    candidate = NotifySettings(
        url=payload.url or stored.url,
        channel=(payload.channel or stored.channel),
    )
    result = await send_notify(candidate, "test")
    return {"ok": result.ok, "detail": result.detail}


@router.get(
    "/schedule",
    response_model=ApplyScheduleOut,
    summary="定时投递配置（默认关闭；只抓队列还是连投递一起）",
)
def read_schedule(db: Session = Depends(get_session)) -> ApplyScheduleOut:
    return _schedule_out(db, load_schedule(db))


@router.put(
    "/schedule",
    response_model=ApplyScheduleOut,
    summary="更新定时投递配置",
)
def update_schedule(payload: ApplyScheduleIn, db: Session = Depends(get_session)) -> ApplyScheduleOut:
    current = load_schedule(db)
    mode = payload.mode if payload.mode in MODE_LABELS else MODE_QUEUE

    # 「抓完并自动投递」属于 T2：必须真的开了自动投递能力，否则**拒绝保存**而不是
    # 存下来再在跑的时候静默降级——用户会以为设好了，实际什么都没发生。
    if mode == MODE_APPLY:
        capabilities = load_settings(db).capabilities
        if not capabilities.is_on("scheduled_apply"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "「定时自动投递」这一档没有开启，不能把模式设成自动投递。"
                    "请先在能力开关里开启「全自动投递」与「定时自动投递」（需要输入确认短语），"
                    "或者把模式改成「只抓岗位入队」。"
                ),
            )

    updated = save_schedule(
        db,
        ScheduleSettings(
            enabled=payload.enabled,
            times=list(payload.times or []),
            mode=mode,
            limit=payload.limit,
            keywords=list(payload.keywords or []),
            city=payload.city,
            fetch_detail=payload.fetch_detail,
            grace_minutes=payload.grace_minutes,
            # 排期信息与"上次跑过"无关，改配置不该把运行记录清掉
            last_run_date=current.last_run_date,
            last_run_at=current.last_run_at,
            last_result=current.last_result,
        ),
    )
    return _schedule_out(db, updated)


@router.post(
    "/attachments",
    summary="上传简历附件（图片）并挂到某个待投任务上",
)
async def upload_attachment(
    file: UploadFile = File(...),
    task_id: int | None = Form(default=None),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """收下**一张图片**作为简历附件。

    只收图片、不收 PDF：把 PDF 转图片需要 PyMuPDF/pdfium 这类库，而本项目有不加重量级
    依赖的纪律。PLAN §6.7 的做法是**在前端用 pdf.js 转换**——图片不出本机，后端零新依赖，
    而且平台看到的确实就是图片简历。

    `task_id` 给了就顺手挂到那条任务上；没给只存文件（用户可以先传、再在队列里挑）。
    """
    data = await file.read()
    try:
        stored = store_attachment(
            get_settings().upload_dir,
            data=data,
            task_id=task_id,
            original_name=file.filename or "",
        )
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if task_id is not None:
        task = db.get(ApplyTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"投递任务不存在：{task_id}")
        task.resume_image_path = stored.relative_path
        db.add(task)
        db.commit()

    return {
        "path": stored.relative_path,
        "size": stored.size,
        "task_id": task_id,
    }


@router.delete("/queue/{task_id}/attachment", summary="取掉某条任务的简历附件")
def detach_attachment(task_id: int, db: Session = Depends(get_session)) -> dict[str, Any]:
    """把附件从任务上摘掉（只断引用，**不删磁盘上的文件**）。

    为什么要有这个：只能挂不能摘的话，传错一张图就没有退路了。而"不删文件"是刻意的——
    同一张图可能被别的任务引用着，删盘会连累它们。
    """
    task = db.get(ApplyTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"投递任务不存在：{task_id}")

    previous = task.resume_image_path
    task.resume_image_path = ""
    db.add(task)
    db.commit()
    return {"task_id": task_id, "removed": bool(previous), "previous": previous}


# --------------------------------------------------------------------------- #
# 账号（多账号轮换）
# --------------------------------------------------------------------------- #
def _mask_proxy(raw: str) -> str:
    """代理的脱敏形式。**凭据绝不回显**：地址里可能带账号密码。"""
    text = (raw or "").strip()
    if not text:
        return ""
    spec, _ = parse_proxy(text)
    if spec is None:
        # 认不出的原样脱敏，不要把它当成可用的地址展示
        return f"{text[:8]}…（无法解析）"
    host = spec.server.split("://", 1)[-1]
    who = "带认证" if spec.has_credentials else "无认证"
    return f"{spec.server.split('://')[0]}://{host}（{who}）"


def _profile_out(db: Session, profile: PlatformProfile) -> ApplyProfileOut:
    meta = load_meta(db).get(str(profile.id), ProfileMeta())
    proxy_ok = True if not meta.proxy else proxy_is_usable(meta.proxy)
    usage = profile_usage_today(db, int(profile.id or 0))
    now = utcnow()
    return ApplyProfileOut(
        id=int(profile.id or 0),
        platform=profile.platform,
        label=profile.label or f"账号 {profile.id}",
        user_data_dir=profile.user_data_dir,
        daily_limit=profile.daily_limit,
        enabled=profile.enabled,
        proxy_configured=bool(meta.proxy),
        masked_proxy=_mask_proxy(meta.proxy),
        locale=meta.locale,
        daily_sent=usage,
        remaining=max(0, profile.daily_limit - usage),
        broken=profile.circuit_broken_until is not None and profile.circuit_broken_until > now,
        circuit_broken_until=profile.circuit_broken_until,
        last_used_at=profile.last_used_at,
        proxy_ok=proxy_ok,
    )


@router.get("/profiles", response_model=ApplyProfilesOut, summary="投递账号列表（多账号轮换）")
def read_profiles(db: Session = Depends(get_session)) -> ApplyProfilesOut:
    settings = load_settings(db)
    rows = list_profiles(db, platform=settings.platform)
    return ApplyProfilesOut(
        items=[_profile_out(db, row) for row in rows],
        configured=bool(rows),
        default_daily_limit=DEFAULT_PROFILE_DAILY_LIMIT,
        locales=[
            {"key": "zh-CN", "label": "中文（中国大陆）"},
            {"key": "zh-TW", "label": "中文（台湾）"},
            {"key": "en-US", "label": "English (US)"},
            {"key": "en-GB", "label": "English (UK)"},
        ],
    )


@router.post("/profiles", response_model=ApplyProfileOut, status_code=201, summary="新增投递账号")
def create_apply_profile(payload: ApplyProfileIn, db: Session = Depends(get_session)) -> ApplyProfileOut:
    profile = create_profile(
        db,
        platform=payload.platform or load_settings(db).platform,
        label=payload.label,
        user_data_dir=payload.user_data_dir,
        daily_limit=payload.daily_limit,
        proxy=payload.proxy,
        locale=payload.locale,
    )
    return _profile_out(db, profile)


@router.put("/profiles/{profile_id}", response_model=ApplyProfileOut, summary="更新投递账号")
def update_apply_profile(
    profile_id: int, payload: ApplyProfileIn, db: Session = Depends(get_session)
) -> ApplyProfileOut:
    profile = db.get(PlatformProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"账号不存在：{profile_id}")

    profile.platform = payload.platform or profile.platform
    profile.label = payload.label
    profile.daily_limit = max(1, int(payload.daily_limit))
    profile.enabled = payload.enabled
    # 目录留空表示"不修改"：清空它会让账号失去自己的登录态
    if payload.user_data_dir.strip():
        profile.user_data_dir = payload.user_data_dir.strip()
    db.add(profile)
    db.commit()
    db.refresh(profile)

    set_profile_meta(db, profile_id, proxy=payload.proxy, locale=payload.locale)
    return _profile_out(db, profile)


@router.delete("/profiles/{profile_id}", status_code=204, summary="删除投递账号")
def delete_apply_profile(profile_id: int, db: Session = Depends(get_session)) -> None:
    """删账号。

    连带把引用它的任务改成"用默认账号"（`profile_id` 置空），而不是拒绝删除：
    用户想删就删，历史任务不该拦住他——但也不能让那些任务指向一个不存在的账号。
    """
    profile = db.get(PlatformProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"账号不存在：{profile_id}")

    for task in db.exec(select(ApplyTask).where(ApplyTask.profile_id == profile_id)).all():
        task.profile_id = None
        db.add(task)

    meta = load_meta(db)
    meta.pop(str(profile_id), None)
    save_meta(db, meta)

    db.delete(profile)
    db.commit()


@router.post("/profiles/probe", summary="探测代理是否真的能用（永远返回 200）")
async def probe_apply_proxy(
    payload: ApplyProxyProbeIn, db: Session = Depends(get_session)
) -> dict[str, Any]:
    """通过代理请求一次"我的 IP 是什么"，把**出口 IP** 告诉用户。

    **永远返回 200**（与 `/system/test-llm`、`/notify/test` 同一个理由）：探测失败是
    正常结果而不是接口错误，这里最需要的正是原始错误。

    为什么必须真发一次请求：地址语法正确 ≠ 能用（密码错、已过期、被防火墙挡都会失败）。
    而"配了但不能用"是最坏的情况——用户以为在隐藏自己。
    """
    raw = payload.proxy.strip()
    if not raw and payload.profile_id is not None:
        raw = load_meta(db).get(str(payload.profile_id), ProfileMeta()).proxy

    if not raw:
        return {"ok": False, "exit_ip": "", "detail": "没有填代理地址", "local_proxy": False}

    result = await probe_proxy(raw, trust_env=get_settings().trust_env_proxy)
    return {
        "ok": result.ok,
        "exit_ip": result.exit_ip,
        "detail": result.detail,
        "local_proxy": result.local_proxy,
    }


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
@router.post(
    "/search",
    response_model=ApplySearchOut,
    dependencies=[Depends(require_local_run)],
    summary="抓岗位 → 过滤 → 打分 → 进待投队列（还没投）",
)
async def search_jobs(
    payload: ApplySearchIn,
    db: Session = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> ApplySearchOut:
    settings = load_settings(db)
    adapter = _build_adapter(settings)
    rules = [_to_filter_rule(row) for row in _ensure_rules(db)]

    try:
        # 单次搜索可以覆盖；没给就用设置里持久化的城市。名字/代码都接受。
        search_city = city_code(payload.city or settings.city)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                # **不能留空**：实测空 city 会被 Boss 判成非法并跳到它自选的城市（我们那次是许昌），
                # 而且那个城市会被记进 profile，之后每次抓取都跟着跑偏。
                city=search_city,
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
                greeted_at=task.greeted_at,
                sent_at=task.sent_at,
                resume_image_path=task.resume_image_path,
                profile_id=task.profile_id,
            )
        )
    return items


@router.get("/queue/snapshot", response_model=QueueSnapshotOut, summary="队列概览")
def read_queue_snapshot(db: Session = Depends(get_session)) -> QueueSnapshotOut:
    return QueueSnapshotOut(**queue_snapshot(db))


@router.post("/queue/delete", summary="从队列里删除若干条（真正删掉，不是标记跳过）")
def delete_queue_tasks(payload: SkipIn, db: Session = Depends(get_session)) -> dict[str, int]:
    return {"deleted": delete_tasks(db, task_ids=payload.task_ids)}


@router.post("/queue/blacklist", summary="拉黑这些任务所属的公司，并把它们从队列移除")
def blacklist_queue_tasks(payload: SkipIn, db: Session = Depends(get_session)) -> dict[str, int]:
    added, removed = blacklist_and_remove(db, task_ids=payload.task_ids)
    return {"blacklisted": added, "deleted": removed}


@router.post(
    "/blacklist/autofill",
    summary="扫描投递记录，自动拉黑明确拒绝过你的公司（受能力开关控制）",
)
def autofill_blacklist_from_rejections(
    dry_run: bool = Query(default=True, description="只预览会被拉黑的公司与依据，不写库"),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """识别「不合适」「岗位已关闭」这类**明确拒绝**并加进黑名单。

    两个安全设计：

    1. **受能力开关控制**：没开启 `autofill_blacklist` 时返回 403 并说明怎么开，
       不会偷偷改用户的黑名单。
    2. **默认只预览**（`dry_run=true`）：误判一次会让用户永远看不到一家本来有戏的公司，
       所以先让他看一眼"会被拉黑谁、凭什么"，再决定要不要真的写。
    """
    settings = load_settings(db)
    if not settings.capabilities.is_on("autofill_blacklist"):
        raise HTTPException(
            status_code=403,
            detail="「识别到拒绝就自动拉黑」没有开启。在投递执行台的能力开关里打开后再试。",
        )

    outcome = autofill_blacklist(db, platform=settings.platform, dry_run=dry_run)
    return {
        "scanned": outcome.scanned,
        "added": outcome.added,
        "dry_run": dry_run,
        "candidates": outcome.candidates,
    }


@router.post("/queue/approve", summary="人工确认（可选替换招呼语）")
def approve(payload: ApproveIn, db: Session = Depends(get_session)) -> dict[str, int]:
    approved = approve_tasks(db, task_ids=payload.task_ids, say_hi_overrides=payload.say_hi_overrides)
    return {"approved": approved}


@router.post("/queue/skip", summary="跳过若干条")
def skip(payload: SkipIn, db: Session = Depends(get_session)) -> dict[str, int]:
    return {"skipped": skip_tasks(db, task_ids=payload.task_ids)}


@router.post(
    "/execute",
    response_model=ExecutionOutcomeOut,
    dependencies=[Depends(require_local_run)],
    summary="执行已确认的投递（受限速与熔断约束）",
)
async def execute(
    payload: ExecuteIn,
    db: Session = Depends(get_session),
) -> ExecutionOutcomeOut:
    settings = load_settings(db)

    # 多账号：**只有能力真的开启时才选号**。没开时走单账号（默认浏览器目录、直连），
    # 行为与引入多账号之前完全一致。
    choice = ProfileChoiceResult(profile=None, reason="")
    multi_on = settings.capabilities.is_on("multi_account")
    if multi_on:
        choice = choose_profile(db, platform=settings.platform)
        if choice.profile is None:
            # 开了多账号但一个可用账号都没有：**明确拒绝**而不是悄悄回落到默认账号——
            # 后者会绕开"按账号记额度与熔断"的全部保护。
            raise HTTPException(status_code=409, detail=f"多账号已开启，但{choice.reason}")

    adapter = _build_adapter(
        settings,
        user_data_dir=choice.profile.user_data_dir if choice.profile else "",
        proxy=choice.proxy,
        fingerprint=choice.fingerprint,
    )
    try:
        outcome = await execute_approved(
            db,
            adapter=adapter,
            settings=settings,
            limit=payload.limit,
            dry_run=payload.dry_run,
            action=payload.action,
        )
    except ApplyDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApplyAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await adapter.close()

    # 记下这个账号被用过、以及投出去几条。选号靠它实现"用到接近限额才换"，
    # 以及"这个号今天还能投几条"。**失败与演练都不算用量**。
    if choice.profile is not None and outcome.sent > 0:
        profile_id = int(choice.profile.id or 0)
        mark_profile_used(db, profile_id)
        for _ in range(outcome.sent):
            bump_profile_usage(db, profile_id)

    # 通知在投递事务之外发：发消息是慢 I/O，而 execute_approved 的约定是短事务。
    # 而且**失败也不影响投递结果**——通知没发出去不该让用户以为投递没成功。
    await _dispatch_notifications(db, settings, outcome)

    response = ExecutionOutcomeOut(
        attempted=outcome.attempted,
        sent=outcome.sent,
        failed=outcome.failed,
        stopped_reason=outcome.stopped_reason,
        tripped=outcome.tripped,
        breaker_message=outcome.breaker_message,
        items=[ExecutionItemOut(**asdict(item)) for item in outcome.items],
    )
    # 多账号时把"这次用了哪个号"告诉前端：多账号出问题时第一个要查的就是这个
    if choice.profile is not None:
        response.profile_label = choice.profile.label or f"账号 {choice.profile.id}"
    for warning in choice.warnings:
        response.notes.append(warning)
    return response


async def _dispatch_notifications(db: Session, settings: ApplySettings, outcome: Any) -> None:
    """把本轮记录下来的通知事件发出去。

    两个"不发"的情况：能力没开启、或用户没配地址。两者都是静默跳过——
    用户没要求通知时不该收到任何东西，也不该因此报错。
    """
    if not settings.capabilities.is_on("notify_webhook"):
        return
    notify_settings = load_notify_settings(db)
    if not notify_settings.configured:
        return

    trust_env = get_settings().trust_env_proxy
    for event in getattr(outcome, "notifications", []):
        kind = str(event.get("kind") or "")
        payload = {key: value for key, value in event.items() if key != "kind"}
        # 单条失败不影响其余事件，也**不影响已经完成的投递**。
        # 正常路径下 send_notification 自己就把异常收敛成 NotifyResult 了，
        # 这里再兜一层是为了防"配置写错导致 httpx 在建 client 时就抛非 HTTP 异常"
        # 这类漏网情况把整个接口变成 500——投递明明已经成功了。
        try:
            await send_notify(notify_settings, kind, trust_env=trust_env, **payload)
        except Exception:  # noqa: BLE001
            logger.exception("发送投递通知失败（事件 %s），已忽略", kind)
