"""平台适配层：新增一个平台 = 新增一个继承 `BrowserAdapter` 的短文件。"""

from .base import ApplyAdapterError, JobCard, PlatformAdapter, SearchQuery, SendResult
from .boss import BossAdapter
from .browser import BrowserAdapter
from .liepin import LiepinAdapter
from .mock import MockPlatformAdapter, default_cards
from .selectors import SELECTOR_SETS, BossSelectors, LiepinSelectors, SelectorSet, load_selectors

#: 可选平台（前端下拉与配置校验都用它）
PLATFORMS: dict[str, type] = {
    "mock": MockPlatformAdapter,
    "boss": BossAdapter,
    "liepin": LiepinAdapter,
}

PLATFORM_LABELS: dict[str, str] = {
    "mock": "Mock 演练（不会真的投递）",
    "boss": "Boss 直聘",
    "liepin": "猎聘",
}

__all__ = [
    "PLATFORMS",
    "PLATFORM_LABELS",
    "SELECTOR_SETS",
    "ApplyAdapterError",
    "BossAdapter",
    "BossSelectors",
    "BrowserAdapter",
    "JobCard",
    "LiepinAdapter",
    "LiepinSelectors",
    "MockPlatformAdapter",
    "PlatformAdapter",
    "SearchQuery",
    "SelectorSet",
    "SendResult",
    "default_cards",
    "load_selectors",
]
