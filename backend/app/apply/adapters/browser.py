"""浏览器适配器的共用部分：patchright 启动、人类化节奏、风控信号识别。

M10 只有 Boss 一个平台，这些逻辑写在 `boss.py` 里没问题；
M14 要接第二个平台（猎聘），如果各自复制一遍，改一个节奏参数就要改两处，
而且「哪个平台漏了验证码检测」这种问题不会有人发现。所以抽到这里。

**子类只需要提供两样东西**：平台的 URL，以及一组选择器。
所有 DOM 相关的字符串都在子类里，这个文件不含任何平台特有的东西。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from ..breaker import BreakerSignal
from .base import ApplyAdapterError, JobCard, SearchQuery, SendResult
from .selectors import SelectorSet

#: 人类化节奏的默认区间（秒）
CLICK_PAUSE = (0.35, 1.1)
TYPE_DELAY = (0.04, 0.18)
SCROLL_PAUSE = (0.6, 1.8)


class BrowserAdapter:
    """基于 patchright + 真实 Chrome 的平台适配器骨架。

    子类要做的：
    - `name`：平台标识（与选择器配置的 key 一致）
    - `adapter_label`：给人看的名字（报错信息里用）
    - `home_url`：搜索页地址（`{keyword}` / `{city}` 会被替换）
    - `selectors`：已校准的选择器集合
    - `docs_hint`：校准文档路径（报错时告诉用户去哪看）
    """

    name = "browser"
    adapter_label = "浏览器"
    home_url = ""
    docs_hint = "docs/APPLY_SELECTORS.md"

    def __init__(
        self,
        *,
        user_data_dir: str,
        selectors: dict[str, str] | None = None,
        headless: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        self._user_data_dir = user_data_dir
        self._selectors: SelectorSet = self._load_selectors(selectors)
        self._headless = headless
        self._rng = rng or random.Random()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    def _load_selectors(self, data: dict[str, str] | None) -> SelectorSet:
        from .selectors import load_selectors

        return load_selectors(self.name, data)

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

    def require(self, *names: str) -> None:
        """检查选择器是否已校准（未校准时报错并指向文档）。"""
        missing = self._selectors.missing(*names)
        if missing:
            raise ApplyAdapterError(
                f"{self.adapter_label}适配器还缺这些选择器："
                + "、".join(missing)
                + f"。请按 {self.docs_hint} 用 DevTools 校准后填入平台配置；"
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
        captcha = getattr(self._selectors, "captcha_marker", "")
        login = getattr(self._selectors, "login_marker", "")
        if captcha and await self._page.locator(captcha).count() > 0:
            return BreakerSignal(kind="captcha", detail="检测到验证码/滑块")
        if login and await self._page.locator(login).count() > 0:
            return BreakerSignal(kind="login_lost", detail="登录态已失效")
        return None

    def _search_url(self, query: SearchQuery) -> str:
        keyword = (query.keywords[0] if query.keywords else "").strip()
        return self.home_url.format(keyword=keyword, city=query.city or "")

    # ------------------------------------------------------------------ #
    # 接口实现
    # ------------------------------------------------------------------ #
    async def ensure_login(self) -> bool:
        await self._ensure_browser()
        login = getattr(self._selectors, "login_marker", "")
        if not login:
            # 没校准就无法判断；保守返回 False，让 UI 提示"请先校准选择器"
            return False
        await self._page.goto(self.home_url.format(keyword="", city=""), wait_until="domcontentloaded")
        await self._pause(SCROLL_PAUSE)
        return await self._page.locator(login).count() == 0

    async def search(self, query: SearchQuery) -> list[JobCard]:
        self.require("search_input", "job_card", "card_title", "card_company")
        await self._ensure_browser()

        await self._page.goto(self._search_url(query), wait_until="domcontentloaded")
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
                    if getattr(self._selectors, "card_company", "")
                    else ""
                )
                salary = (
                    (await card.locator(self._selectors.card_salary).inner_text()).strip()
                    if getattr(self._selectors, "card_salary", "")
                    else ""
                )
                link = (
                    await card.locator(self._selectors.card_link).get_attribute("href")
                    if getattr(self._selectors, "card_link", "")
                    else ""
                )
                city = (
                    (await card.locator(self._selectors.card_city).inner_text()).strip()
                    if getattr(self._selectors, "card_city", "")
                    else ""
                )
            except Exception as exc:  # noqa: BLE001 - 单张卡片失败不该中断整轮
                # 抓到一半失败通常意味着页面结构变了 → 明确报"适配失效"，而不是静默少抓
                raise ApplyAdapterError(
                    f"解析第 {index + 1} 张岗位卡片失败（{exc}）。这通常意味着页面结构变了，请重新校准选择器。"
                ) from exc

            results.append(
                JobCard(
                    external_id=str(link or f"{self.name}-{index}"),
                    company=company,
                    title=title,
                    jd_url=link or "",
                    salary_range=salary,
                    city=city,
                )
            )

        await self._pause(SCROLL_PAUSE)
        return results

    async def fetch_detail(self, card: JobCard) -> JobCard:
        self.require("detail_jd")
        await self._ensure_browser()
        if not card.jd_url:
            return card

        await self._page.goto(card.jd_url, wait_until="domcontentloaded")
        await self._pause(SCROLL_PAUSE)

        signal = await self._signal_or_none()
        if signal is not None:
            raise ApplyAdapterError(f"打开岗位详情时命中风控信号：{signal.message}")

        card.jd_text = (await self._page.locator(self._selectors.detail_jd).inner_text()).strip()

        for field_name in ("detail_experience", "detail_degree", "hr_name"):
            selector = getattr(self._selectors, field_name, "")
            if selector:
                try:
                    value = (await self._page.locator(selector).inner_text()).strip()
                except Exception:  # noqa: BLE001 - 该字段缺失不算错误
                    value = ""
                if field_name == "detail_experience":
                    card.experience_req = value
                elif field_name == "detail_degree":
                    card.degree_req = value
                else:
                    card.hr_name = value

        if getattr(self._selectors, "hr_active", ""):
            card.hr_active = await self._page.locator(self._selectors.hr_active).count() > 0
        if getattr(self._selectors, "headhunter_marker", ""):
            card.is_headhunter = await self._page.locator(self._selectors.headhunter_marker).count() > 0

        return card

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None) -> SendResult:
        self.require("greet_button", "greet_input", "greet_send")
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
