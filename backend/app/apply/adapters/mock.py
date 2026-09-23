"""Mock 平台适配器：没有真实浏览器也能把"确认式投递"整条流程跑通。

它按配置生成岗位卡片，并且 **可以注入故障**（第 N 次发送返回验证码/掉线信号）——
这样熔断逻辑能在测试与冒烟里被真实触发，而不是靠"读代码觉得应该对"。
这是 M0 以来一直坚持的做法：Mock 不是占位符，是让整条链路可被验证的基础设施。
"""

from __future__ import annotations

import asyncio
from typing import Sequence

from ..breaker import BreakerSignal
from .base import JobCard, SearchQuery, SendResult

#: 默认生成的岗位（覆盖"会通过过滤"和"会被过滤拦掉"两种情况）
DEFAULT_JOBS: tuple[dict[str, object], ...] = (
    {
        "external_id": "mk-001",
        "company": "某某科技",
        "title": "后端工程师（Python）",
        "salary_range": "25-40K",
        "city": "杭州",
        "experience_req": "3-5年",
        "degree_req": "本科",
        "company_size": "500-999人",
        "company_stage": "已上市",
        "industry": "企业服务",
        "hr_name": "张女士",
        "hr_active": True,
        "is_headhunter": False,
        "jd_text": "负责订单核心链路，要求熟悉 Python、高并发、Redis 与 MySQL；有性能优化经验优先。",
    },
    {
        "external_id": "mk-002",
        "company": "某某网络",
        "title": "服务端开发工程师",
        "salary_range": "20-35K·14薪",
        "city": "上海",
        "experience_req": "3-5年",
        "degree_req": "本科",
        "company_size": "100-499人",
        "company_stage": "B轮",
        "industry": "互联网",
        "hr_name": "李先生",
        "hr_active": True,
        "is_headhunter": False,
        "jd_text": "参与交易系统建设，熟悉 Go 或 Python；了解消息队列与分布式事务。",
    },
    {
        "external_id": "mk-003",
        "company": "某某外包",
        "title": "Java 开发（驻场）",
        "salary_range": "12-18K",
        "city": "南京",
        "experience_req": "1-3年",
        "degree_req": "大专",
        "company_size": "1000-9999人",
        "company_stage": "不需要融资",
        "industry": "人力资源服务",
        "hr_name": "王经理",
        "hr_active": False,
        "is_headhunter": True,
        "jd_text": "外派到客户现场，长期驻场开发，接受加班，可接受应届。",
    },
)


def default_cards() -> list[JobCard]:
    return [JobCard(**job) for job in DEFAULT_JOBS]  # type: ignore[arg-type]


class MockPlatformAdapter:
    name = "mock"

    def __init__(
        self,
        *,
        cards: Sequence[JobCard] | None = None,
        logged_in: bool = True,
        #: 第几次发送开始失败（1 表示第一次就失败）；None 表示一直成功
        fail_from_attempt: int | None = None,
        fail_signal: BreakerSignal | None = None,
        send_delay_seconds: float = 0.0,
    ) -> None:
        self._cards = list(cards) if cards is not None else default_cards()
        self._logged_in = logged_in
        self._fail_from_attempt = fail_from_attempt
        self._fail_signal = fail_signal or BreakerSignal(kind="captcha", detail="Mock：模拟出现验证码")
        self._send_delay = send_delay_seconds
        self.attempts = 0
        self.sent: list[str] = []

    async def ensure_login(self) -> bool:
        return self._logged_in

    async def search(self, query: SearchQuery) -> list[JobCard]:
        results = self._cards
        if query.keywords:
            lowered = [word.lower() for word in query.keywords]
            filtered = [
                card
                for card in results
                if any(word in f"{card.title}{card.jd_text}".lower() for word in lowered)
            ]
            if filtered:
                results = filtered
        return results[: query.limit] if query.limit > 0 else list(results)

    async def fetch_detail(self, card: JobCard) -> JobCard:
        return card

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None) -> SendResult:
        self.attempts += 1
        if self._send_delay > 0:
            await asyncio.sleep(self._send_delay)

        if self._fail_from_attempt is not None and self.attempts >= self._fail_from_attempt:
            signal = self._fail_signal
            return SendResult(ok=False, detail=f"Mock：第 {self.attempts} 次发送失败（{signal.message}）", signal=signal)

        self.sent.append(card.external_id)
        suffix = "（含图片简历）" if resume_image else ""
        return SendResult(ok=True, detail=f"Mock：已向 {card.company} 发送招呼语{suffix}")

    async def close(self) -> None:
        """Mock 没有浏览器要关。显式实现是为了满足 PlatformAdapter 协议——
        API 层对适配器一视同仁地 close()，Mock 缺这个方法会在 finally 里炸掉。"""
        return None
