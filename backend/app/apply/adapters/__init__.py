"""平台适配层：新增一个平台 = 新增一个实现 PlatformAdapter 的模块。"""

from .base import ApplyAdapterError, JobCard, PlatformAdapter, SearchQuery, SendResult
from .boss import BossAdapter
from .mock import MockPlatformAdapter, default_cards
from .selectors import SELECTOR_SETS, BossSelectors, LiepinSelectors, SelectorSet, load_selectors

__all__ = [
    "SELECTOR_SETS",
    "ApplyAdapterError",
    "BossAdapter",
    "BossSelectors",
    "JobCard",
    "LiepinSelectors",
    "MockPlatformAdapter",
    "PlatformAdapter",
    "SearchQuery",
    "SelectorSet",
    "SendResult",
    "default_cards",
    "load_selectors",
]
