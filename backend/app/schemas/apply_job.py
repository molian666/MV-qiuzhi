"""半自动投递的对外模型（M10）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApplyCapabilitiesIn(BaseModel):
    """能力开关的写入模型。

    `enabled` 只表达"想开哪些"；高风险档位还需要 `signed_phrases` 里带上对应的确认短语。
    短语**故意不做自动补签**——用户必须真的打一遍那句话（见 `apply/capabilities.py`）。
    """

    enabled: dict[str, bool] = Field(default_factory=dict)
    #: 用户本次输入并确认的短语（累积保存，不会被后来的请求清空）
    signed_phrases: list[str] = Field(default_factory=list)
    #: 是否在本地运行。云端一律拿不到 T3 档能力。
    run_local: bool = True
    #: 验证码策略：circuit（默认，熔断停投当天）/ wait（暂停等人工处理）。
    #: **没有"自动绕过"这个取值**。
    captcha_mode: str = "circuit"


class CapabilityOut(BaseModel):
    """单个能力的对外表示：既有定义，也有"现在能不能用"。"""

    key: str
    label: str
    tier: int
    tier_label: str
    tier_summary: str
    summary: str
    default: bool = False
    requires: list[str] = Field(default_factory=list)
    #: 用户是否勾选了这个能力
    wanted: bool = False
    #: 勾了**且**闸门通过 —— 前端据此显示"已生效"
    on: bool = False
    #: 没生效时的可操作原因（缺风险确认 / 缺哪句短语 / 缺依赖 / 需本地运行）
    blocked_reason: str = ""
    #: 需要用户输入的那句确认短语（仅当原因就是"缺短语"时非空）
    missing_phrase: str = ""


class ApplyCapabilitiesOut(BaseModel):
    """能力面板需要的全部信息，一次取回（免得前端硬编码中文与档位）。"""

    items: list[CapabilityOut] = Field(default_factory=list)
    risk_acknowledged: bool = False
    signed_phrases: list[str] = Field(default_factory=list)
    run_local: bool = True
    captcha_mode: str = "circuit"
    captcha_modes: list[dict[str, str]] = Field(default_factory=list)
    tier_labels: dict[str, str] = Field(default_factory=dict)
    tier_summaries: dict[str, str] = Field(default_factory=dict)
    #: 是否有任一"自动"档能力真的生效（前端据此显示醒目的运行中提示）
    any_auto_enabled: bool = False


class ApplySettingsIn(BaseModel):
    enabled: bool = False
    risk_acknowledged: bool = False
    platform: str = "mock"
    default_channel: str = "boss"
    want_salary_min: int = 0
    #: 搜索用城市代码（Boss 如 101010100=北京）；留空＝用平台当前城市
    city: str = ""
    #: 静默模式：visible / offscreen / headless
    window_mode: str = "visible"
    per_minute: int = 3
    per_day: int = 30
    jitter_min_seconds: float = 3.0
    jitter_max_seconds: float = 9.0
    selectors: dict[str, str] = Field(default_factory=dict)
    user_data_dir: str = ""
    #: 分级能力开关。**不传即视为全关**（与老前端兼容：老前端不知道这个字段）。
    capabilities: ApplyCapabilitiesIn | None = None


class ApplySettingsOut(BaseModel):
    enabled: bool
    risk_acknowledged: bool
    platform: str
    default_channel: str
    want_salary_min: int
    city: str = ""
    window_mode: str = "visible"
    per_minute: int
    per_day: int
    jitter_min_seconds: float
    jitter_max_seconds: float
    selectors: dict[str, str] = Field(default_factory=dict)
    user_data_dir: str = ""
    #: 硬上限（配置会被夹到这个范围内，避免一键把自己投废）
    max_per_minute: int = 10
    max_per_day: int = 60
    #: 能力开关的完整状态（含每个能力的生效情况与未生效原因）
    capabilities: ApplyCapabilitiesOut = Field(default_factory=ApplyCapabilitiesOut)


class ApplyNotifySettingsIn(BaseModel):
    """通知配置的写入模型。地址由用户自己填（企业微信机器人 webhook 或通用 URL）。"""

    url: str = ""
    channel: str = "wechat"
    #: 订阅哪些事件。成功类默认关——每投一条响一次手机会让人把通知整个关掉。
    on_sent: bool = False
    on_circuit: bool = True
    on_captcha: bool = True
    on_failed: bool = True


class ApplyNotifySettingsOut(ApplyNotifySettingsIn):
    #: 地址是否已配置
    configured: bool = False
    #: 地址的脱敏形式：**不回显完整地址**，webhook URL 里的 key 等同于凭据
    masked_url: str = ""
    channels: list[dict[str, str]] = Field(default_factory=list)


class ApplyNotifyTestIn(BaseModel):
    """发一条测试通知。留空则用当前保存的配置。"""

    url: str = ""
    channel: str = ""


class ApplyScheduleIn(BaseModel):
    """定时投递配置。`times` 是 "HH:MM" 列表（一天最多几个，见 schedule.MAX_SLOTS_PER_DAY）。"""

    enabled: bool = False
    times: list[str] = Field(default_factory=list)
    #: queue = 只抓岗位入队（不投递）；apply = 抓完并自动投递（要 T2 闸门）
    mode: str = "queue"
    limit: int = 10
    keywords: list[str] = Field(default_factory=list)
    city: str = ""
    fetch_detail: bool = False
    grace_minutes: int = 30


class ApplyScheduleOut(ApplyScheduleIn):
    #: 解析后的时间点（认不出的已经被丢掉，界面能看出实际生效的是哪些）
    parsed_times: list[str] = Field(default_factory=list)
    description: str = ""
    active: bool = False
    next_run_at: datetime | None = None
    due_now: bool = False
    last_run_at: datetime | None = None
    last_result: str = ""
    mode_labels: dict[str, str] = Field(default_factory=dict)
    max_slots: int = 6


class ApplyProfileIn(BaseModel):
    """新建 / 更新一个投递账号（多账号轮换的基本单位）。"""

    platform: str = "boss"
    label: str = ""
    #: 浏览器用户数据目录。**每个账号必须独立**——共用等于共用一个登录态。
    #: 留空则按 profile id 自动生成。
    user_data_dir: str = ""
    daily_limit: int = 20
    enabled: bool = True
    #: 代理 `http://user:pass@host:port`。留空 = 直连。
    proxy: str = ""
    locale: str = "zh-CN"


class ApplyProfileOut(BaseModel):
    id: int
    platform: str
    label: str
    user_data_dir: str
    daily_limit: int
    enabled: bool
    #: 代理**不回显凭据**：只给"配了没有" + 脱敏形式。地址里可能带账号密码。
    proxy_configured: bool = False
    masked_proxy: str = ""
    locale: str = "zh-CN"
    #: 今日已投 / 剩余额度（按账号算）
    daily_sent: int = 0
    remaining: int = 0
    #: 是否处在熔断期
    broken: bool = False
    circuit_broken_until: datetime | None = None
    last_used_at: datetime | None = None
    #: 代理是否可用。**不可用时这个账号不会被选中**（别裸奔）。
    proxy_ok: bool = True


class ApplyProfilesOut(BaseModel):
    """账号列表 + 面板需要的元信息。"""

    items: list[ApplyProfileOut] = Field(default_factory=list)
    #: 有没有任何账号。没有时投递走单账号（默认浏览器目录、直连）。
    configured: bool = False
    default_daily_limit: int = 20
    locales: list[dict[str, str]] = Field(default_factory=list)


class ApplyProxyProbeIn(BaseModel):
    """探测一个代理是否真的能用。留空则用某个账号已配的代理。"""

    proxy: str = ""
    profile_id: int | None = None


class ApplyRuleIn(BaseModel):
    name: str = ""
    kind: str = "keyword"
    enabled: bool = True
    priority: int = 100
    params: dict[str, Any] = Field(default_factory=dict)


class ApplyRuleOut(ApplyRuleIn):
    id: int


class ApplySearchIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    city: str = ""
    salary_min: int = 0
    limit: int = Field(default=20, ge=1, le=60)
    fetch_detail: bool = False
    resume_id: int | None = None


class CandidateOut(BaseModel):
    job_posting_id: int
    external_id: str
    company: str
    title: str
    salary_range: str = ""
    city: str = ""
    jd_url: str = ""
    matched_score: float = 0.0
    passed: bool = True
    blocked_by: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    say_hi: str = ""
    task_id: int | None = None


class ApplySearchOut(BaseModel):
    fetched: int = 0
    passed: int = 0
    blocked: int = 0
    queued: int = 0
    duplicates: int = 0
    candidates: list[CandidateOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ApproveIn(BaseModel):
    task_ids: list[int] = Field(min_length=1)
    say_hi_overrides: dict[int, str] = Field(default_factory=dict)


class SkipIn(BaseModel):
    task_ids: list[int] = Field(min_length=1)


class ExecuteIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=30)
    dry_run: bool = False
    #: M17：投递动作拆分。greet=只打招呼（→greeted），resume=投递简历（→sent+落投递记录）。
    action: str = Field(default="resume", pattern="^(greet|resume)$")


class QueueTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    company: str = ""
    title: str = ""
    salary_range: str = ""
    city: str = ""
    jd_url: str = ""
    matched_score: float | None = None
    say_hi_text: str = ""
    attempts: int = 0
    error: str = ""
    created_at: datetime
    approved_at: datetime | None = None
    greeted_at: datetime | None = None
    sent_at: datetime | None = None
    #: 这条任务挂的简历附件（相对数据目录的路径）。空 = 投递时发平台的在线简历。
    #: 界面靠它显示"这条带不带附件"，否则用户没法确认自己传的图有没有生效。
    resume_image_path: str = ""
    #: 这条任务由哪个账号投（多账号）。空 = 默认账号。
    profile_id: int | None = None


class QueueSnapshotOut(BaseModel):
    total: int = 0
    pending: int = 0
    approved: int = 0
    greeted: int = 0
    sent: int = 0
    failed: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)


class ExecutionItemOut(BaseModel):
    task_id: int
    company: str = ""
    title: str = ""
    ok: bool = False
    detail: str = ""


class ExecutionOutcomeOut(BaseModel):
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    stopped_reason: str = ""
    tripped: bool = False
    breaker_message: str = ""
    items: list[ExecutionItemOut] = Field(default_factory=list)
    #: 多账号：这次用的是哪个账号（空 = 单账号模式）。
    #: 多账号出问题时第一个要查的就是"这条是哪个号投的"。
    profile_label: str = ""
    #: 本轮需要提醒用户的事（例如代理指向本机、出口 IP 不会变）
    notes: list[str] = Field(default_factory=list)


class ApplyStatusOut(BaseModel):
    enabled: bool = False
    risk_acknowledged: bool = False
    platform: str = "mock"
    circuit_broken_until: datetime | None = None
    broken: bool = False
    daily_sent: int = 0
    daily_limit: int = 30
    remaining_quota: int = 0
    adapter_ready: bool = True
    adapter_note: str = ""
