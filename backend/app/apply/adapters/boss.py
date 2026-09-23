"""Boss 直聘适配器（patchright 驱动真实 Chrome）。

⚠️ **诚实声明**：选择器没有在真实页面上校准过（开发机没有浏览器自动化环境）。
所以本实现的策略是"**选择器全部外置**"，未配置时明确报错，而不是瞎猜——
编一套看起来能用、实际第一次跑就崩的选择器，比先留空更糟。

已经真实可用的部分：
- 用真实 Chrome（`channel="chrome"`）+ 持久化上下文 + 有头模式（官方推荐的反检测配置，
  内置 Chromium 反而更容易被识别）；
- 人类化节奏：随机延迟、逐字输入、点击前后的停顿；
- 登录态持久化（扫码一次能用很久）；
- 登录失效/验证码的**信号识别**（交给熔断器决定当天是否停投）。

接入步骤（等你提供真实页面时我来补选择器并真机验证）：
1. 按 docs/APPLY_SELECTORS.md 用 DevTools 取选择器，填进平台的 selectors 配置；
2. 跑一次 `POST /api/apply/search` 看能不能抓到岗位；
3. 用 Mock 适配器验证过的队列/限速/熔断逻辑不需要任何改动。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from ..breaker import BreakerSignal
from .base import ApplyAdapterError, JobCard, SearchQuery, SendResult
from .selectors import BossSelectors, load_selectors

BOSS_HOME = "https://www.zhipin.com/web/geek/job"

#: 人类化节奏的默认区间（秒）
CLICK_PAUSE = (0.35, 1.1)
TYPE_DELAY = (0.04, 0.18)
SCROLL_PAUSE = (0.6, 1.8)


class BossAdapter:
    name = "boss"

    def __init__(
        self,
        *,
        user_data_dir: str,
        selectors: dict[str, str] | None = None,
        headless: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        self._user_data_dir = user_data_dir
        self._selectors: BossSelectors = load_selectors("boss", selectors)  # type: ignore[assignment]
        self._headless = headless
        self._rng = rng or random.Random()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------ #
    # 基础设施
    # ------------------------------------------------------------------ #
    async def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            from patchright.async_api import async_playwright  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 依赖未装时给出明确指引
            raise ApplyAdapterError(
                "未安装 patchright。请执行 `pip install patchright` 并 `patchright install chrome`；"
                "在此之前可以先用 Mock 适配器验证队列与限速逻辑。"
            ) from exc

        self._playwright = await async_playwright().start()
        # 官方最佳实践：真实 Chrome + 持久化上下文 + 有头 + 不注入指纹
        self._context = await self._playwright.chromium.launch_persistent_context(
            self._user_data_dir,
            channel="chrome",
            headless=self._headless,
            no_viewport=True,
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    def _require(self, *names: str) -> None:
        missing = self._selectors.missing(*names)
        if missing:
            raise ApplyAdapterError(
                "Boss 适配器还缺这些选择器："
                + "、".join(missing)
                + "。请按 docs/APPLY_SELECTORS.md 用 DevTools 校准后填入平台配置；"
                "在此之前可以先用 Mock 适配器跑通整条确认式投递流程。"
            )

    async def _pause(self, window: tuple[float, float]) -> None:
        await asyncio.sleep(self._rng.uniform(*window))

    async def _type_like_human(self, selector: str, text: str) -> None:
        await self._page.click(selector)
        for char in text:
            await self._page.type(selector, char, delay=self._rng.uniform(*TYPE_DELAY) * 1000)

    async def _signal_or_none(self) -> BreakerSignal | None:
        """检查页面是否出现了"平台在警告你"的标志。"""
        if self._selectors.captcha_marker:
            if await self._page.locator(self._selectors.captcha_marker).count() > 0:
                return BreakerSignal(kind="captcha", detail="检测到验证码/滑块")
        if self._selectors.login_marker:
            if await self._page.locator(self._selectors.login_marker).count() > 0:
                return BreakerSignal(kind="login_lost", detail="登录态已失效")
        return None

    # ------------------------------------------------------------------ #
    # 接口实现
    # ------------------------------------------------------------------ #
    async def ensure_login(self) -> bool:
        await self._ensure_browser()
        if not self._selectors.login_marker:
            # 没校准就无法判断；保守返回 False，让 UI 提示"请先校准选择器"
            return False
        await self._page.goto(BOSS_HOME, wait_until="domcontentloaded")
        await self._pause(SCROLL_PAUSE)
        return await self._page.locator(self._selectors.login_marker).count() == 0

    async def search(self, query: SearchQuery) -> list[JobCard]:
        self._require("search_input", "job_card", "card_title", "card_company")
        await self._ensure_browser()

        keyword = (query.keywords[0] if query.keywords else "").strip()
        url = f"{BOSS_HOME}?query={keyword}"
        if query.city:
            url += f"&city={query.city}"
        await self._page.goto(url, wait_until="domcontentloaded")
        await self._pause(SCROLL_PAUSE)

        signal = await self._signal_or_none()
        if signal is not None:
            raise ApplyAdapterError(f"打开搜索页时命中风控信号：{signal.message}")

        cards = self._page.locator(self._selectors.job_card)
        total = min(await cards.count(), max(1, query.limit))
        results: list[JobCard] = []

        for index in range(total):
            card = cards.nth(index)
            try:
                title = (await card.locator(self._selectors.card_title).inner_text()).strip()
                company = (
                    (await card.locator(self._selectors.card_company).inner_text()).strip()
                    if self._selectors.card_company
                    else ""
                )
                salary = (
                    (await card.locator(self._selectors.card_salary).inner_text()).strip()
                    if self._selectors.card_salary
                    else ""
                )
                link = (
                    await card.locator(self._selectors.card_link).get_attribute("href")
                    if self._selectors.card_link
                    else ""
                )
            except Exception as exc:  # noqa: BLE001 - 单张卡片失败不该中断整轮
                # 抓到一半失败通常意味着页面结构变了 → 明确报"适配失效"，而不是静默少抓
                raise ApplyAdapterError(
                    f"解析第 {index + 1} 张岗位卡片失败（{exc}）。这通常意味着页面结构变了，请重新校准选择器。"
                ) from exc

            results.append(
                JobCard(
                    external_id=str(link or f"boss-{index}"),
                    company=company,
                    title=title,
                    jd_url=link or "",
                    salary_range=salary,
                )
            )

        await self._pause(SCROLL_PAUSE)
        return results

    async def fetch_detail(self, card: JobCard) -> JobCard:
        self._require("detail_jd")
        await self._ensure_browser()
        if not card.jd_url:
            return card

        await self._page.goto(card.jd_url, wait_until="domcontentloaded")
        await self._pause(SCROLL_PAUSE)

        signal = await self._signal_or_none()
        if signal is not None:
            raise ApplyAdapterError(f"打开岗位详情时命中风控信号：{signal.message}")

        text = (await self._page.locator(self._selectors.detail_jd).inner_text()).strip()
        if self._selectors.detail_experience:
            card.experience_req = (await self._page.locator(self._selectors.detail_experience).inner_text()).strip()
        if self._selectors.detail_degree:
            card.degree_req = (await self._page.locator(self._selectors.detail_degree).inner_text()).strip()
        if self._selectors.hr_name:
            card.hr_name = (await self._page.locator(self._selectors.hr_name).inner_text()).strip()
        if self._selectors.hr_active:
            card.hr_active = await self._page.locator(self._selectors.hr_active).count() > 0
        if self._selectors.headhunter_marker:
            card.is_headhunter = await self._page.locator(self._selectors.headhunter_marker).count() > 0

        card.jd_text = text
        return card

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None) -> SendResult:
        self._require("greet_button", "greet_input", "greet_send")
        await self._ensure_browser()

        # 发送前先看一眼有没有风控标志——这是"不要带病继续投"的第一道闸
        signal = await self._signal_or_none()
        if signal is not None:
            return SendResult(ok=False, detail=signal.message, signal=signal)

        try:
            await self._page.click(self._selectors.greet_button)
            await self._pause(CLICK_PAUSE)
            await self._type_like_human(self._selectors.greet_input, text)
            await self._pause(CLICK_PAUSE)
            await self._page.click(self._selectors.greet_send)
            await self._pause(CLICK_PAUSE)
        except Exception as exc:  # noqa: BLE001
            # 操作失败到底是"页面变了"还是"账号被限制"，这里区分不了 →
            # 交给调用方看页面标志；兜底报 page_changed（不熔断当天）
            signal = await self._signal_or_none() or BreakerSignal(kind="page_changed", detail=str(exc))
            return SendResult(ok=False, detail=str(exc), signal=signal)

        signal = await self._signal_or_none()
        if signal is not None:
            return SendResult(ok=False, detail=signal.message, signal=signal)

        return SendResult(ok=True, detail=f"已向 {card.company} 发送招呼语")

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._playwright is not None:
                await self._playwright.stop()
        finally:
            self._context = None
            self._page = None
            self._playwright = None
