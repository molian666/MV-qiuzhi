"""浏览器适配器的共用部分：patchright 启动、人类化节奏、风控信号识别。

M10 只有 Boss 一个平台，这些逻辑写在 `boss.py` 里没问题；
M14 要接第二个平台（猎聘），如果各自复制一遍，改一个节奏参数就要改两处，
而且「哪个平台漏了验证码检测」这种问题不会有人发现。所以抽到这里。

**子类只需要提供两样东西**：平台的 URL，以及一组选择器。
所有 DOM 相关的字符串都在子类里，这个文件不含任何平台特有的东西。
"""

from __future__ import annotations

import asyncio
import functools
import random
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar
from urllib.parse import urljoin

from ..breaker import BreakerSignal
from ..proxy import Fingerprint, parse_proxy
from ..pua import has_obfuscated, strip_obfuscated
from .base import ApplyAdapterError, JobCard, SearchQuery, SendResult
from .selectors import SelectorSet

_T = TypeVar("_T")

#: 人类化节奏的默认区间（秒）
CLICK_PAUSE = (0.35, 1.1)
TYPE_DELAY = (0.04, 0.18)
SCROLL_PAUSE = (0.6, 1.8)


#: 静默模式的三种取值（持久化在设置里）
WINDOW_MODES = ("visible", "offscreen", "headless")


def window_args(mode: str) -> list[str]:
    """按静默模式给出 Chrome 启动参数（配合 `headless` 使用）。

    **为什么要有"移到屏幕外"这一档**：无头模式最省事，但也**最容易被风控识别**——
    我们实测过：用无头去查一次登录态，直接陷进 Boss 的 `请稍候` 循环（有头窗口同一时间是正常的）。
    所以想在后台跑，优先用 `offscreen`：**仍然是有头的真实 Chrome**（对平台而言与正常浏览无异），
    只是窗口挪到屏幕外，不占你的屏幕。

    注意：多显示器/超大虚拟屏时 `-2400,-2400` 有可能落在另一块屏幕上，
    那种情况请改用 `headless`（代价是更容易触发验证）。
    """
    if mode == "offscreen":
        return ["--window-position=-2400,-2400", "--window-size=1280,900"]
    return []


def is_headless_mode(mode: str) -> bool:
    return (mode or "").strip().lower() == "headless"


def loop_supports_subprocess(loop: asyncio.AbstractEventLoop) -> bool:
    """这个事件循环能不能创建子进程（playwright 启动浏览器靠的就是它）。"""
    if sys.platform != "win32":
        return True
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    return proactor is not None and isinstance(loop, proactor)


#: Chrome 发现"同一 user-data-dir 已有实例"时的提示（它会把请求交给那个实例，然后自己退出）。
#: 中英文都匹配：Chrome 的界面语言决定 Playwright 抓到的日志文本。
_PROFILE_BUSY_MARKERS = (
    "existing browser session",
    "已在现有的浏览器会话",
    "ProcessSingleton",
    "SingletonLock",
)


def _launch_error(exc: Exception, user_data_dir: str, adapter_label: str) -> ApplyAdapterError:
    """把"启动浏览器失败"翻译成用户能照着做的错误。

    实测最常见的一种：profile 被另一个 Chrome 占用时，Chrome 会打印
    「已在现有的浏览器会话中打开。」然后**立刻退出**（exitCode=0），
    Playwright 只看到一个浏览器进程死了 → `TargetClosedError: Target page, context or
    browser has been closed`。这个报错完全看不出原因，会以 500 + 一大段 traceback 抛给用户。
    """
    text = str(exc)
    if any(marker in text for marker in _PROFILE_BUSY_MARKERS):
        return ApplyAdapterError(
            f"打不开浏览器：登录用的用户数据目录正被另一个 Chrome 占用（{user_data_dir}）。"
            "常见原因：你自己开着这个目录的 Chrome 窗口，或者上一次自动化没退干净"
            "（比如探针/上一次抓取还在等）。"
            "先把那个窗口关掉再试；实在找不到就等 1 分钟，或在设置里把 user_data_dir 换一个目录"
            "（换了要重新登录一次）。"
        )
    return ApplyAdapterError(
        f"{adapter_label}启动浏览器失败（{type(exc).__name__}）：{text[:300]}"
    )


def isolated_browser_loop(func: Callable[..., Coroutine[Any, Any, _T]]) -> Callable[..., Coroutine[Any, Any, _T]]:
    """把浏览器自动化放进"自带 Proactor 循环"的线程里执行。

    **为什么必须这么做**（这是实测踩出来的，不是设计洁癖）：
    uvicorn 在 Windows 上跑 `--reload`（或 `--workers > 1`）时用的是 **SelectorEventLoop**
    （`uvicorn/loops/asyncio.py`：`use_subprocess=True` → `SelectorEventLoop`），而它
    **不支持 asyncio 子进程**：`asyncio.create_subprocess_exec()` 会直接抛
    `NotImplementedError`。patchright 启动浏览器正是通过创建子进程，于是
    `POST /api/apply/search` 必然 500 —— 和选择器无关，纯粹是事件循环不兼容。
    而 `scripts/dev.ps1` 恰好永远带 `--reload`，所以这条链路此前在 Windows 上从未跑通。

    **为什么隔离而不是改全局 loop 策略**：uvicorn 是把 loop 工厂直接传进
    `asyncio.Runner(loop_factory=...)` 的，设置 policy 未必生效；而且换掉主循环会影响
    这个应用里其它同样吃事件循环的东西（实时语音 WebSocket、文件上传）。这里只在
    "当前循环建不了子进程"时才启用独立线程，其余情况（单元测试、Linux、不带 --reload
    的本地脚本）保持原来的内联执行，行为不变。
    """

    @functools.wraps(func)
    async def wrapper(self: "BrowserAdapter", *args: Any, **kwargs: Any) -> _T:
        return await self._run_browser_call(func(self, *args, **kwargs))

    return wrapper

#: 真正的风控/验证页（跳到这些地址就是"平台在要你过验证"，命中即熔断停投当天）。
#: 清单来自 get_jobs 的实战记录：密集遍历后会被弹到 `/web/passport/zp/verify.html`（极验滑块）。
RISK_URL_PATTERNS = (
    "/web/user/safe/verify-slider",
    "/web/passport/zp/verify",
    "/web/passport/zp/security",
)
#: 风控**标记**：会出现在正常页面的 URL 上（实测页面渲染正常时也带 `_security_check=1`），
#: 所以只有"带标记 + 页面没有岗位内容"才当成风控。
RISK_MARKER_PATTERNS = ("_security_check=", "security-check")


def drop_empty_query_params(url: str) -> str:
    """去掉值为空的查询参数。

    为什么必须做：`home_url` 模板把 city 直接拼进 URL，城市为空时会拼出 `&city=`。
    实测这个空参数会让 Boss **跳到它自己挑的城市**（我们那次是许昌，而不是用户所在城市）
    并在 URL 上挂 `_security_check=`。宁可不传这个参数，让平台按账号当前城市走。
    """
    if "?" not in url:
        return url
    head, _, query = url.partition("?")
    kept = [pair for pair in query.split("&") if pair and not re.fullmatch(r"[^=]+=", pair)]
    return f"{head}?{'&'.join(kept)}" if kept else head


#: 明确"长期不活跃"的说法（看到这些字样才判定为不活跃）
HR_DEAD_MARKERS = ("半年前", "年前", "长期未", "3月前", "3个月前", "6月前", "个月未", "年初", "去年")
#: 最近活跃的说法
HR_RECENT_MARKERS = ("在线", "刚刚", "本周", "今日", "今天", "1日内", "3日内", "本月", "活跃")


def is_target_closed(exc: BaseException) -> bool:
    """异常是不是"浏览器/页面被关掉了"。

    实测场景：后端开着 `--reload` 时改代码 → worker 重启 → 浏览器进程被杀，
    报错是 `Page.click: Target page, context or browser has been closed`；
    用户手动关掉那个 Chrome 窗口也是同一条报错。
    """
    text = f"{type(exc).__name__}: {exc}"
    return any(
        marker in text
        for marker in ("TargetClosedError", "has been closed", "Target page, context or browser")
    )


def closed_detail(exc: BaseException) -> str:
    """把"浏览器中途被关掉"翻译成用户能判断的一句话。"""
    return (
        f"浏览器在操作过程中被关闭了（{type(exc).__name__}）：**本轮已停止，没有发送任何消息**。"
        "常见原因：后端开着 --reload 且代码刚被改动（worker 重启会连带杀掉浏览器），"
        "或者那个 Chrome 窗口被手动关掉了。确认之后重新点「开始投递」即可。"
    )


def _attachment_problem(resume_image: str | None) -> str:
    """附件路径有没有问题。返回空串表示没问题。

    **单独抽成函数**是为了能在不起浏览器的情况下测它：把不存在的路径交给
    `set_input_files` 会得到一个与真实原因无关的错误，而这里最该说清楚的是
    "你选的简历文件没了"。不带附件（None / 空串）是**合法**的——那表示发平台上的在线简历。
    """
    if not resume_image:
        return ""
    if not Path(resume_image).is_file():
        return f"简历附件不存在：{resume_image}。请在投递执行台重新选择附件。"
    return ""


def hr_active_from_status(text: str) -> bool | None:
    """从招聘者的活跃状态文本判断活跃与否。

    **关键：不能把"页面上没有标记"当成"不活跃"。**
    实测 `span.boss-online-tag` 只在 HR **当前在线**时才存在，而 Boss 的活跃状态还有
    "刚刚活跃/本周活跃/3日内活跃/半年前活跃"等不同写法——按"存在即活跃"实现时，
    所有当前不在线的 HR 都被判成 False（＝长期不活跃），于是几乎每个岗位都被
    「HR 需活跃」拦掉（用户截图里一片「已拦：HR 长期不活跃」就是这么来的）。

    所以：看到最近活跃的说法 → True；看到明确长期不活跃的说法 → False；
    **什么都没看到 → None（不知道）**，而规则只在 `False` 时才拦。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    # 先判"死"：像"半年前活跃"这种也含"活跃"二字，顺序反了会被误判成活跃
    if any(marker in raw for marker in HR_DEAD_MARKERS):
        return False
    if any(marker in raw for marker in HR_RECENT_MARKERS):
        return True
    return None


def merge_salary(list_salary: str, list_obfuscated: bool, detail_salary: str) -> tuple[str, bool]:
    """决定最终用哪个薪资文本。

    **为什么值得单独一个函数**：实测列表页的薪资数字被平台用自定义字体换成了私用区码点
    （`\\ue032\\ue033-\\ue032\\ue039K·\\ue032\\ue034薪`），而**同一岗位的详情页是正常文本**
    （`12-18K·13薪`）。所以详情页一旦取到值就用它——这直接决定"薪资区间"过滤规则是否生效。
    详情页也混淆时保留标记（不猜数字）。
    """
    cleaned_detail = strip_obfuscated(detail_salary)
    if cleaned_detail:
        return cleaned_detail, has_obfuscated(detail_salary)
    return list_salary, list_obfuscated


#: JD 的关键词：同一个选择器常常同时命中公司介绍/薪资范围/公司详情，
#: 靠它把"真正的岗位描述"挑出来（实测一条岗位页面上 `div.job-sec-text` 命中 3 个，
#: 分别是公司介绍、薪资范围、公司详情——取第一个会拿到公司介绍）
JD_KEYWORDS = ("岗位职责", "任职要求", "职位描述", "工作职责", "职位要求", "工作内容", "岗位要求")

#: 兜底：有些岗位没写 JD，但页面上会有"薪资范围：10000-15000"这种公司声明
SALARY_TEXT_JS = r"""
() => {
  const re = /(?:薪资|薪酬|月薪)\s*范围?\s*[:：]\s*([0-9][0-9,\.]*\s*[-~至]\s*[0-9][0-9,\.]*\s*[Kk千万]?)/;
  for (const el of document.querySelectorAll('div, span, p, li')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 40) continue;
    const m = t.match(re);
    if (m) return m[1].trim();
  }
  return '';
}
"""

#: 在一个容器里按"内容像不像 JD"挑文本：
#: 1) 有 JD 关键词的，取最长的那条；
#: 2) 全都没有关键词时，把明显的公司介绍/薪资声明排掉（否则会把公司简介当 JD 塞进 jd_text），
#:    剩下的取最长；再没有就返回空——**空着比塞错强**（下游的匹配打分与招呼语会用到它）。
BEST_TEXT_JS = r"""
(selector) => {
  const keywords = %s;
  const noise = ['公司介绍', '公司简介', '工商信息', '薪资范围', '公司地址', '公司福利'];
  const nodes = Array.from(document.querySelectorAll(selector));
  if (!nodes.length) return '';
  const texts = nodes.map((n) => (n.innerText || '').trim()).filter(Boolean);
  if (!texts.length) return '';

  const withKeywords = texts.filter((t) => keywords.some((k) => t.includes(k)));
  if (withKeywords.length) {
    return withKeywords.sort((a, b) => b.length - a.length)[0];
  }
  const rest = texts.filter((t) => !noise.some((w) => t.includes(w)) && !/成立于\s*\d{4}/.test(t));
  return rest.length ? rest.sort((a, b) => b.length - a.length)[0] : '';
}
""" % (list(JD_KEYWORDS),)


def risk_url_signal(url: str, *, has_job_cards: bool = False) -> BreakerSignal | None:
    """按 URL 判断是否撞上风控（页面内容可用于区分"标记"和"真被拦"）。

    区分两级的原因：实测 `_security_check=1` 会挂在**渲染正常**的结果页 URL 上，
    如果只看标记就熔断，会把一次正常抓取当成风控；反过来漏掉它，就会带着坏页面继续投。
    所以：验证页地址 → 直接熔断；只有标记且页面**没有**岗位内容 → 熔断。
    """
    text = (url or "").lower()
    for pattern in RISK_URL_PATTERNS:
        if pattern in text:
            return BreakerSignal(kind="captcha", detail=f"被跳到风控/验证页：{url}")
    for pattern in RISK_MARKER_PATTERNS:
        if pattern in text and not has_job_cards:
            return BreakerSignal(
                kind="captcha",
                detail=f"URL 带风控标记（{pattern}）且页面没有岗位内容：{url}",
            )
    return None


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
        extra_args: list[str] | None = None,
        rng: random.Random | None = None,
        proxy: str = "",
        fingerprint: Fingerprint | None = None,
    ) -> None:
        self._user_data_dir = user_data_dir
        self._selectors: SelectorSet = self._load_selectors(selectors)
        self._headless = headless
        #: 额外的 Chrome 启动参数（静默模式的"窗口移到屏幕外"就是靠它）
        self._extra_args = list(extra_args or [])
        self._rng = rng or random.Random()
        #: 这个账号的代理（`http://user:pass@host:port`）。空 = 直连。
        self._proxy_raw = (proxy or "").strip()
        #: 与账号绑定的浏览器特征。None = 交给 patchright 的默认行为
        #: （默认更安全：真实 Chrome 自己的特征本来就是一整套自洽的）。
        self._fingerprint = fingerprint
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        #: 独立浏览器循环（只在主循环建不了子进程时创建，见 isolated_browser_loop）
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # 代理与指纹
    # ------------------------------------------------------------------ #
    def _context_options(self) -> dict[str, Any]:
        """`launch_persistent_context` 的代理参数。

        **代理配置不对就直接拒绝启动**，而不是忽略它继续跑：忽略的后果是请求走真实 IP，
        而用户以为自己在用代理——那比不用代理更糟（他会对"已经在隐藏自己"产生错误的安全感）。
        """
        if not self._proxy_raw:
            return {}

        spec, problem = parse_proxy(self._proxy_raw)
        if spec is None:
            raise ApplyAdapterError(
                f"代理配置不可用（{problem}）。**没有启动浏览器**——"
                "带着坏掉的代理继续跑会让请求走你的真实 IP，而你以为已经在用代理了。"
                "请在账号设置里修正代理地址，或把它清空（表示直连）。"
            )
        return {"proxy": spec.to_playwright()}

    def _launch_args(self) -> list[str]:
        """最终的 Chrome 启动参数。

        指纹的视口要在这里注入，而不是用 `viewport=` 参数：持久化上下文配合
        `no_viewport=True`（必须保持它，否则用户手动调整过的窗口大小会被强行覆盖），
        `viewport=` 会被忽略，只有 `--window-size` 真正生效。
        """
        args = list(self._extra_args)
        if self._fingerprint is None:
            return args

        # 已经有 --window-size 的（offscreen 模式会带上）不覆盖：
        # "把窗口挪到屏幕外"是这个模式的全部意义，尺寸跟它走就好。
        if any(arg.startswith("--window-size") for arg in args):
            return args

        width, height = self._fingerprint.viewport_width, self._fingerprint.viewport_height
        args.append(f"--window-size={width},{height}")
        return args

    def _fingerprint_options(self) -> dict[str, Any]:
        """只下发语言与时区。

        **不下发 `viewport=`**（见 `_launch_args` 的说明），也**不默认下发 user_agent**：
        真实 Chrome 的 UA 与它自己的一整套特征是一致的，改它反而更容易露馅。
        `Fingerprint.to_context_args` 里仍保留 UA 字段，供确有需要时显式设置。
        """
        if self._fingerprint is None:
            return {}
        return {
            "locale": self._fingerprint.locale,
            "timezone_id": self._fingerprint.timezone_id,
        }

    # ------------------------------------------------------------------ #
    # 事件循环隔离（Windows + uvicorn --reload 下必须）
    # ------------------------------------------------------------------ #
    async def _run_browser_call(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """在当前循环能建子进程时直接跑，否则挪到独立线程的 Proactor 循环里跑。"""
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is not None and loop_supports_subprocess(current):
            return await coro

        loop = self._ensure_browser_loop()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))

    def _ensure_browser_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        proactor = getattr(asyncio, "ProactorEventLoop", None)
        loop = proactor() if sys.platform == "win32" and proactor is not None else asyncio.new_event_loop()
        # call_soon_threadsafe 在循环还没跑起来时也能排队，所以这里不必等线程就绪
        thread = threading.Thread(target=self._serve_loop, args=(loop,), name="apply-browser-loop", daemon=True)
        thread.start()
        self._loop, self._loop_thread = loop, thread
        return loop

    @staticmethod
    def _serve_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _shutdown_browser_loop(self) -> None:
        """停掉独立循环。**必须从它自己的线程之外调用**（否则会 join 自己）。"""
        loop, thread = self._loop, self._loop_thread
        self._loop = self._loop_thread = None
        if loop is None:
            return
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        if not loop.is_closed():
            loop.close()

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
        try:
            # 代理配置不对时 `_context_options()` 会抛——**故意放在 try 之外调用一次**，
            # 让"配置错误"和"浏览器起不来"是两种可区分的失败（前者是用户要改配置，
            # 后者通常是 Chrome 被占用）。
            context_options = self._context_options()
        except ApplyAdapterError:
            raise

        args = self._launch_args()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                self._user_data_dir,
                channel="chrome",
                headless=self._headless,
                no_viewport=True,
                **({"args": args} if args else {}),
                **context_options,
                **self._fingerprint_options(),
            )
        except Exception as exc:  # noqa: BLE001 - 启动失败必须变成可读错误，而不是 500
            # 失败后必须把驱动收掉：否则 self._playwright 指向一个死驱动，
            # 重试时会再 start 一个、把旧的驱动进程泄漏掉。
            await self._reset_browser_state()
            raise _launch_error(exc, self._user_data_dir, self.adapter_label) from exc
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def _reset_browser_state(self) -> None:
        """把浏览器相关的引用清干净（可重复调用）。"""
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._context = None
        self._page = None
        self._playwright = None

    # ------------------------------------------------------------------ #
    # 读元素：一律"取第一个"，绝不让 strict 模式把整次抓取弄崩
    # ------------------------------------------------------------------ #
    async def _first_text(self, selector: str, *, root: Any = None) -> str:
        """读第一个匹配元素的文本（命中多个也不报错）。

        **为什么不用 `locator(sel).inner_text()`**：Playwright 的 locator 默认 **strict 模式**，
        选择器命中多个元素时直接抛错。实测 `div.job-sec-text` 在有些岗位页上命中 3 个
        （公司介绍 / 薪资范围 / 公司详情），于是整次抓取 500、一行岗位都没入库。
        平台页面不由我们控制，所以这里把"选得准不准"和"会不会崩"分开：
        读取永远只取第一个，选得准不准交给选择器与内容挑选（见 `_best_text`）。
        """
        locator = (root or self._page).locator(selector)
        try:
            if await locator.count() == 0:
                return ""
            return (await locator.first.inner_text()).strip()
        except Exception:  # noqa: BLE001 - 读不到就是空，不要中断整轮
            return ""

    async def _first_attr(self, selector: str, name: str, *, root: Any = None) -> str:
        locator = (root or self._page).locator(selector)
        try:
            if await locator.count() == 0:
                return ""
            return await locator.first.get_attribute(name) or ""
        except Exception:  # noqa: BLE001
            return ""

    async def _best_text(self, selector: str) -> str:
        """在多个命中里按内容挑最像 JD 的那个（关键词优先，其次最长）。"""
        try:
            return (await self._page.evaluate(BEST_TEXT_JS, selector)) or ""
        except Exception:  # noqa: BLE001
            return ""

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
        """检查页面是否出现了"平台在警告你"的标志。

        先看 URL（风控跳转本身就是最硬的信号），再看选择器标记。
        **整个检查是"顺便看看"，不能因为它本身出错而把调用方带崩**：
        浏览器/页面已经没了的时候，后续真正的操作会报出准确的错，这里返回 None 就好。
        """
        try:
            has_cards = False
            job_card = getattr(self._selectors, "job_card", "")
            if job_card:
                try:
                    has_cards = await self._page.locator(job_card).count() > 0
                except Exception:  # noqa: BLE001 - 页面正在跳转时查询会失败，按"没有内容"处理
                    has_cards = False
            url_signal = risk_url_signal(getattr(self._page, "url", ""), has_job_cards=has_cards)
            if url_signal is not None:
                return url_signal

            captcha = getattr(self._selectors, "captcha_marker", "")
            login = getattr(self._selectors, "login_marker", "")
            if captcha and await self._page.locator(captcha).count() > 0:
                return BreakerSignal(kind="captcha", detail="检测到验证码/滑块")
            if login and await self._page.locator(login).count() > 0:
                return BreakerSignal(kind="login_lost", detail="登录态已失效")
            return None
        except Exception:  # noqa: BLE001
            return None

    def _search_url(self, query: SearchQuery) -> str:
        keyword = (query.keywords[0] if query.keywords else "").strip()
        return drop_empty_query_params(
            self.home_url.format(keyword=keyword, city=query.city or "")
        )

    def _absolute_url(self, href: str) -> str:
        """把卡片里的相对链接补成绝对地址。

        平台的岗位链接是 `/job_detail/xxx.html`，而：
        - `fetch_detail` 直接 `page.goto(card.jd_url)`，相对地址会被 Playwright 拒绝；
        - 前端把 `jd_url` 渲染成 `<a href>`，相对地址会指到前端自己的 5173 上。
        参考项目 get_jobs 也是手动拼 `"https://www.zhipin.com" + href`。
        """
        href = (href or "").strip()
        if not href:
            return ""
        return urljoin(self.home_url or "", href)

    # ------------------------------------------------------------------ #
    # 接口实现
    # ------------------------------------------------------------------ #
    @isolated_browser_loop
    async def ensure_login(self) -> bool:
        await self._ensure_browser()
        login = getattr(self._selectors, "login_marker", "")
        if not login:
            # 没校准就无法判断；保守返回 False，让 UI 提示"请先校准选择器"
            return False
        await self._page.goto(self.home_url.format(keyword="", city=""), wait_until="domcontentloaded")
        await self._pause(SCROLL_PAUSE)
        return await self._page.locator(login).count() == 0

    @isolated_browser_loop
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
                title = strip_obfuscated(await self._first_text(self._selectors.card_title, root=card))
                company = (
                    strip_obfuscated(await self._first_text(self._selectors.card_company, root=card))
                    if getattr(self._selectors, "card_company", "")
                    else ""
                )
                # 薪资常被平台用自定义字体混淆：这里只清掉不可见的私用区码点，
                # **不猜数字**（解错的薪资比解不出更糟），同时把"被混淆过"这件事
                # 用 salary_obfuscated 带出去——清理后原文里就看不出来了。
                raw_salary = (
                    await self._first_text(self._selectors.card_salary, root=card)
                    if getattr(self._selectors, "card_salary", "")
                    else ""
                )
                salary = strip_obfuscated(raw_salary)
                link = (
                    self._absolute_url(await self._first_attr(self._selectors.card_link, "href", root=card))
                    if getattr(self._selectors, "card_link", "")
                    else ""
                )
                city = (
                    strip_obfuscated(await self._first_text(self._selectors.card_city, root=card))
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
                    salary_obfuscated=has_obfuscated(raw_salary),
                    city=city,
                )
            )

        await self._pause(SCROLL_PAUSE)
        return results

    @isolated_browser_loop
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

        try:
            # JD 用"按内容挑"：同一个选择器常同时命中公司介绍/薪资范围/公司详情
            # （实测命中 3 个），纯粹取第一个会拿到公司介绍
            card.jd_text = strip_obfuscated(await self._best_text(self._selectors.detail_jd))

            for field_name in ("detail_experience", "detail_degree", "hr_name", "detail_salary"):
                selector = getattr(self._selectors, field_name, "")
                if not selector:
                    continue
                value = await self._first_text(selector)
                if field_name == "detail_experience":
                    card.experience_req = value
                elif field_name == "detail_degree":
                    card.degree_req = value
                elif field_name == "detail_salary":
                    # 详情页薪资是干净文本（实测），用它覆盖列表页那份被字体混淆的
                    card.salary_range, card.salary_obfuscated = merge_salary(
                        card.salary_range, bool(card.salary_obfuscated), value
                    )
                else:
                    card.hr_name = value

            # 兜底：有些岗位没写 JD 容器，但页面上有"薪资范围：10000-15000"这种公司声明。
            # 只有在"手上这份薪资连数字都没有"时才走这一步，避免覆盖上面更准的值。
            if not card.salary_obfuscated and not re.search(r"\d", card.salary_range or ""):
                fallback = await self._salary_text_from_page()
                if fallback:
                    card.salary_range, card.salary_obfuscated = merge_salary(
                        card.salary_range, False, fallback
                    )

            if getattr(self._selectors, "hr_active", ""):
                # 读状态文本再判断，而不是"有标记就算活跃"：
                # 判据细节见 hr_active_from_status 的说明（缺席≠不活跃）
                card.hr_active = hr_active_from_status(
                    await self._first_text(self._selectors.hr_active)
                )
            if getattr(self._selectors, "headhunter_marker", ""):
                card.is_headhunter = await self._page.locator(self._selectors.headhunter_marker).count() > 0
        except ApplyAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 详情页读取失败是"我们自己的适配问题"（不是账号问题）→ 交给 runner 记一条 note
            # 并继续处理下一个岗位，**绝不能变成 500 让整轮抓取白跑**
            raise ApplyAdapterError(f"读取详情页字段失败（{type(exc).__name__}）：{exc}") from exc

        return card

    async def _salary_text_from_page(self) -> str:
        try:
            return (await self._page.evaluate(SALARY_TEXT_JS)) or ""
        except Exception:  # noqa: BLE001
            return ""

    @isolated_browser_loop
    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None) -> SendResult:
        self.require("greet_button", "greet_input", "greet_send")
        await self._ensure_browser()

        try:
            # ⚠️ **必须先导航到这个岗位的详情页。**
            # 执行投递是**另一次请求、另一个浏览器会话**，不是"抓取时那个页面还开着"：
            # 不导航就是在 about:blank 上找「立即沟通」——实测第一条因此 30 秒超时；
            # 而轮到第二条时页面还停在上一条岗位，会把招呼语发给**上一个 HR**（更严重）。
            # 导航本身也放在 try 里：浏览器**在导航前就已经死了**（热重载杀进程/窗口被关掉）
            # 时，这里会直接抛错——不兜住就会从"一次发送失败"升级成整轮 500。
            if card.jd_url:
                await self._page.goto(card.jd_url, wait_until="domcontentloaded")
                await self._pause(SCROLL_PAUSE)

            # 发送前先看一眼有没有风控标志——这是"不要带病继续投"的第一道闸
            signal = await self._signal_or_none()
            if signal is not None:
                return SendResult(ok=False, detail=signal.message, signal=signal)

            pages_before = list(self._context.pages)
            await self._page.click(self._selectors.greet_button)
            await self._pause(CLICK_PAUSE)

            # 有的平台点「立即沟通」会**新开一个标签页**（聊天页）。输入框在新页上，
            # 还盯着旧页会一直等不到元素——所以切到新开的那个页。
            opened = [page for page in self._context.pages if page not in pages_before]
            if opened:
                self._page = opened[-1]
                await self._pause(SCROLL_PAUSE)

            await self._type_like_human(self._selectors.greet_input, text)
            await self._pause(CLICK_PAUSE)
            await self._page.click(self._selectors.greet_send)
            await self._pause(CLICK_PAUSE)
        except Exception as exc:  # noqa: BLE001
            if is_target_closed(exc):
                # 浏览器都没了，再去问页面有没有风控标志只会再抛一次
                return SendResult(
                    ok=False,
                    detail=closed_detail(exc),
                    signal=BreakerSignal(kind="page_changed", detail=str(exc)),
                )
            # 操作失败到底是"页面变了"还是"账号被限制"，这里区分不了 →
            # 交给调用方看页面标志；兜底报 page_changed（不熔断当天）
            signal = await self._signal_or_none() or BreakerSignal(kind="page_changed", detail=str(exc))
            return SendResult(ok=False, detail=str(exc), signal=signal)

        signal = await self._signal_or_none()
        if signal is not None:
            return SendResult(ok=False, detail=signal.message, signal=signal)

        return SendResult(ok=True, detail=f"已向 {card.company} 发送招呼语")

    @isolated_browser_loop
    async def send_resume(self, card: JobCard, resume_image: str | None) -> SendResult:
        """把简历发出去。

        **这个方法存在的理由**：`send_greeting` 收了 `resume_image` 参数却从头到尾没用过——
        也就是说界面上那个「投递简历」按钮其实只发了一句招呼语，然后记成一次投递。
        用户以为简历发出去了，实际没有。所以附件投递单独成一个动作，并且
        **拿不到选择器就直接报错**，而不是安静地降级成"只打了招呼"。
        """
        self.require("resume_send_button")
        await self._ensure_browser()

        # 附件路径必须真的存在：把不存在的路径交给 set_input_files 会得到一个
        # 与真实原因无关的报错，而这里最该说清楚的是"你选的简历文件没了"。
        problem = _attachment_problem(resume_image)
        if problem:
            return SendResult(ok=False, detail=problem)

        try:
            if card.jd_url:
                await self._page.goto(card.jd_url, wait_until="domcontentloaded")
                await self._pause(SCROLL_PAUSE)

            signal = await self._signal_or_none()
            if signal is not None:
                return SendResult(ok=False, detail=signal.message, signal=signal)

            pages_before = list(self._context.pages)
            await self._page.click(self._selectors.resume_send_button)
            await self._pause(CLICK_PAUSE)

            # 点「发送简历」常会弹出一个附件选择层，或者新开一个标签页
            opened = [page for page in self._context.pages if page not in pages_before]
            if opened:
                self._page = opened[-1]
                await self._pause(SCROLL_PAUSE)

            if resume_image:
                if not self._selectors.resume_upload_input:
                    return SendResult(
                        ok=False,
                        detail=(
                            "还没校准「简历附件上传框」选择器（resume_upload_input），"
                            f"无法发送本地附件。按 {self.docs_hint} 校准后再试；"
                            "在此之前可以留空附件，改发平台上的在线简历。"
                        ),
                    )
                # 文件输入框通常被 CSS 藏起来：直接设值，不点它
                await self._page.set_input_files(self._selectors.resume_upload_input, resume_image)
                await self._pause(SCROLL_PAUSE)

            if self._selectors.resume_confirm_button:
                await self._page.click(self._selectors.resume_confirm_button)
                await self._pause(CLICK_PAUSE)
        except Exception as exc:  # noqa: BLE001
            if is_target_closed(exc):
                return SendResult(
                    ok=False,
                    detail=closed_detail(exc),
                    signal=BreakerSignal(kind="page_changed", detail=str(exc)),
                )
            # 适配问题（选择器失效 / 页面结构变了）不该熔断当天，交给调用方记一条错
            signal = await self._signal_or_none() or BreakerSignal(kind="page_changed", detail=str(exc))
            return SendResult(ok=False, detail=str(exc), signal=signal)

        signal = await self._signal_or_none()
        if signal is not None:
            return SendResult(ok=False, detail=signal.message, signal=signal)

        # 有标记就按标记确认；没有标记时**不谎报成功**——我们不能确认对方收到了简历
        if self._selectors.resume_sent_marker:
            if await self._page.locator(self._selectors.resume_sent_marker).count() == 0:
                return SendResult(
                    ok=False,
                    detail=(
                        "点了发送简历，但页面上没有出现「已发送」的标记，无法确认对方是否收到。"
                        "请到平台上看一眼这次沟通，再决定要不要重试。"
                    ),
                )

        what = "本地简历附件" if resume_image else "平台在线简历"
        return SendResult(ok=True, detail=f"已向 {card.company} 发送{what}")

    async def close(self) -> None:
        """关闭浏览器并回收独立循环。

        `close` 不能直接挂 `@isolated_browser_loop`：清理完还要 `join` 那个循环线程，
        而 join 自己会死锁。所以这里显式分两步：先在浏览器循环里关掉浏览器，再在外面停循环。
        """
        try:
            await self._run_browser_call(self._close_browser())
        finally:
            self._shutdown_browser_loop()

    async def _close_browser(self) -> None:
        await self._reset_browser_state()
