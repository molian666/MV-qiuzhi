"""熔断：一旦看到风控信号，当天就停投。

这条规则来自 get_jobs 作者的实战提醒（也是我们最该抄的一条经验）：
Boss 掉线/验证码之后继续投，账号当天就可能被限制。
所以这里的策略是**保守到近乎保守过头**——命中就停到当天结束，第二天再手动开。

区分两类信号：
- `login_lost`（登录态失效）、`captcha`（验证码）、`rate_limited`（频率限制）→ **必须熔断**；
- `page_changed`（页面结构变了）、`unknown` → 只停当前任务并把错误显式报到 UI，不停当天。
  因为页面改版是我们的适配问题，不该惩罚用户的账号。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: 这些信号意味着"平台在警告你"，必须当天停投
TRIPPING_KINDS = frozenset({"login_lost", "captcha", "rate_limited"})

KIND_LABELS: dict[str, str] = {
    "login_lost": "登录态失效",
    "captcha": "出现验证码/滑块",
    "rate_limited": "平台提示操作过于频繁",
    "page_changed": "页面结构变化（适配失效）",
    "unknown": "未知异常",
}


@dataclass(slots=True)
class BreakerSignal:
    kind: str
    detail: str = ""

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def message(self) -> str:
        return f"{self.label}：{self.detail}" if self.detail else self.label


def should_trip(signal: BreakerSignal | None) -> bool:
    if signal is None:
        return False
    return signal.kind in TRIPPING_KINDS


def circuit_until(now: datetime) -> datetime:
    """熔断到当天结束（本地日期 23:59:59）。"""
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    if end_of_day <= now:
        end_of_day = now + timedelta(seconds=1)
    return end_of_day


def is_broken(broken_until: datetime | None, *, now: datetime) -> bool:
    return broken_until is not None and broken_until > now


def describe(signal: BreakerSignal) -> str:
    """给用户看的一句话 + 该做什么。"""
    if signal.kind == "login_lost":
        return f"{signal.message}。请在弹出的浏览器里重新扫码登录，然后明天再继续投。"
    if signal.kind == "captcha":
        return f"{signal.message}。请手动完成验证；**今天不要再自动投递**，避免账号被限制。"
    if signal.kind == "rate_limited":
        return f"{signal.message}。今天停投，明天再试；也可以把每分钟上限调低。"
    if signal.kind == "page_changed":
        return f"{signal.message}。这是适配问题（不是账号问题）：请重新校准选择器。"
    return f"{signal.message}。已停止本次投递。"
