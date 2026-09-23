"""半自动投递模块（M10）。

分工：
- `filters`   过滤链（投不投这个岗位）
- `throttler` 限速（现在能不能投）
- `breaker`   熔断（账号是不是该歇了）
- `say_hi`    招呼语（投的时候说什么）
- `adapters/` 平台操作（怎么投）—— Mock 可跑通全流程，boss 待校准选择器
- `runner`    编排（串起上面这些 + 落库 + 人工确认闸门）
"""

from .breaker import BreakerSignal, circuit_until, is_broken, should_trip
from .filters import FilterRule, FilterVerdict, JobFacts, evaluate, parse_salary
from .throttler import (
    ThrottleConfig,
    ThrottleDecision,
    ThrottleState,
    can_send,
    next_delay_seconds,
    record_send,
)

__all__ = [
    "BreakerSignal",
    "FilterRule",
    "FilterVerdict",
    "JobFacts",
    "ThrottleConfig",
    "ThrottleDecision",
    "ThrottleState",
    "can_send",
    "circuit_until",
    "evaluate",
    "is_broken",
    "next_delay_seconds",
    "parse_salary",
    "record_send",
    "should_trip",
]
