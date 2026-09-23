"""平台适配层：每个平台一个实现，接口统一。

职责边界很关键：**这里只负责"怎么操作网站"**（登录、搜索、抓详情、发招呼语），
不负责"投不投、投什么"（那是过滤链与队列的事）。
这样平台改版时只需要动这一个文件，而投递策略与风控逻辑完全不受影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..breaker import BreakerSignal


class ApplyAdapterError(RuntimeError):
    """适配器无法完成操作（未登录、选择器没校准、页面结构变了等）。"""


@dataclass(slots=True)
class SearchQuery:
    keywords: list[str] = field(default_factory=list)
    city: str = ""
    salary_min: int = 0
    experience: str = ""
    limit: int = 30


@dataclass(slots=True)
class JobCard:
    """一个岗位（搜索结果 +（可选）详情页补全）。"""

    external_id: str
    company: str = ""
    title: str = ""
    jd_url: str = ""
    jd_text: str = ""
    salary_range: str = ""
    city: str = ""
    experience_req: str = ""
    degree_req: str = ""
    company_size: str = ""
    company_stage: str = ""
    industry: str = ""
    hr_name: str = ""
    hr_active: bool | None = None
    is_headhunter: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SendResult:
    ok: bool
    detail: str = ""
    #: 失败时给出熔断判断依据（登录失效/验证码/频率限制……）
    signal: BreakerSignal | None = None


@runtime_checkable
class PlatformAdapter(Protocol):
    name: str

    async def ensure_login(self) -> bool:
        """登录态是否可用（不可用时应引导用户去扫码）。"""
        ...

    async def search(self, query: SearchQuery) -> list[JobCard]: ...

    async def fetch_detail(self, card: JobCard) -> JobCard: ...

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None) -> SendResult: ...

    async def close(self) -> None: ...
