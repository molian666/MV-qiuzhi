"""平台健壮性：从真机采集中发现的 6 个问题，各自必须有回归测试。

这些 bug 的共同点是**静默**——不报错，只是结果悄悄不对：
1. 空 `city=` 参数让 Boss 跳到它自选城市；
2. 岗位链接是相对路径 → 抓详情必然失败、前端链接指错；
3. 风控跳转（`_security_check` / 滑块页）不被识别 → 带着坏页面继续投；
4. 薪资被平台字体反爬 → 薪资规则静默失效；
5. 招呼语生成失败被吞掉 → 用户以为那是 AI 写的；
6. `--reload` 下的事件循环不支持子进程 → 抓候选直接 500（这个不静默，但根因很隐蔽）。

前四条能纯函数级验证；第 5 条要用 runner 端到端跑 notes；第 6 条要真的在
`SelectorEventLoop` 上创建一次子进程才能复现。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from app.apply.adapters.boss import BossAdapter
from app.apply.adapters.browser import (
    BrowserAdapter,
    drop_empty_query_params,
    risk_url_signal,
)
from app.apply.adapters.base import ApplyAdapterError, JobCard, SearchQuery
from app.apply.filters import FilterRule, parse_salary
from app.apply.pua import describe_obfuscation, has_obfuscated, strip_obfuscated
from app.apply.runner import ApplySettings, search_and_queue
from app.apply.throttler import ThrottleConfig
from app.db import session_scope
from app.providers.llm.base import LLMClient, LLMMessage, LLMResult

#: 真实抓到的薪资文本（Boss 字体反爬）：`20-30K·14薪` 在 DOM 里长这样
REAL_OBFUSCATED_SALARY = "\ue032\ue033-\ue032\ue039K·\ue032\ue034薪"


# --------------------------------------------------------------------------- #
# 1. 空查询参数
# --------------------------------------------------------------------------- #
def test_drop_empty_query_params_removes_city_only() -> None:
    url = "https://www.zhipin.com/web/geek/job?query=%E5%90%8E%E7%AB%AF&city="
    assert drop_empty_query_params(url) == "https://www.zhipin.com/web/geek/job?query=%E5%90%8E%E7%AB%AF"


def test_drop_empty_query_params_keeps_non_empty_values() -> None:
    url = "https://www.zhipin.com/web/geek/job?query=x&city=101010100"
    assert drop_empty_query_params(url) == url


def test_drop_empty_query_params_leaves_urlless_query_alone() -> None:
    assert drop_empty_query_params("https://www.liepin.com/zhaopin/?key=python") == (
        "https://www.liepin.com/zhaopin/?key=python"
    )
    assert drop_empty_query_params("https://www.zhipin.com/") == "https://www.zhipin.com/"


def test_boss_search_url_omits_city_when_empty() -> None:
    """空 city 不传，而不是传 `city=`：实测空参数会让 Boss 跳到它自选的城市。"""
    adapter = BossAdapter(user_data_dir="unused", selectors={})
    with_city = adapter._search_url(SearchQuery(keywords=["机器视觉"], city="101010100"))
    without_city = adapter._search_url(SearchQuery(keywords=["机器视觉"], city=""))

    assert "city=101010100" in with_city
    assert "city=" not in without_city
    assert "query=" in without_city


# --------------------------------------------------------------------------- #
# 2. 相对链接
# --------------------------------------------------------------------------- #
def test_relative_job_link_is_absolutized() -> None:
    adapter = BossAdapter(user_data_dir="unused", selectors={})
    assert adapter._absolute_url("/job_detail/61945b12a104c2d1031_2Ni0FFNU.html") == (
        "https://www.zhipin.com/job_detail/61945b12a104c2d1031_2Ni0FFNU.html"
    )
    # 已经是绝对地址就不要动它
    absolute = "https://www.zhipin.com/job_detail/abc.html"
    assert adapter._absolute_url(absolute) == absolute
    assert adapter._absolute_url("") == ""


def test_liepin_search_url_has_no_city_template() -> None:
    from app.apply.adapters.liepin import LiepinAdapter

    adapter = LiepinAdapter(user_data_dir="unused", selectors={})
    assert adapter._search_url(SearchQuery(keywords=["python"], city="")) == (
        "https://www.liepin.com/zhaopin/?key=python"
    )


# --------------------------------------------------------------------------- #
# 3. 风控信号
# --------------------------------------------------------------------------- #
def test_slider_and_verify_pages_trip_breaker() -> None:
    for url in (
        "https://www.zhipin.com/web/user/safe/verify-slider?x=1",
        "https://www.zhipin.com/web/passport/zp/verify.html",
        "https://www.zhipin.com/web/passport/zp/security.html?code=37",
    ):
        signal = risk_url_signal(url, has_job_cards=True)
        assert signal is not None, url
        assert signal.kind == "captcha"


def test_security_check_marker_trips_only_without_content() -> None:
    """`_security_check=` 会挂在**渲染正常**的结果页上（实测），所以要看内容一起判。"""
    marker = "https://www.zhipin.com/web/geek/jobs?query=x&city=101010100&_security_check=1_1790570510705"

    assert risk_url_signal(marker, has_job_cards=True) is None
    tripped = risk_url_signal(marker, has_job_cards=False)
    assert tripped is not None
    assert tripped.kind == "captcha"


def test_normal_url_is_not_a_signal() -> None:
    assert risk_url_signal("https://www.zhipin.com/web/geek/job?query=x", has_job_cards=False) is None


# --------------------------------------------------------------------------- #
# 4. 薪资字体反爬
# --------------------------------------------------------------------------- #
def test_detect_and_strip_obfuscated_salary() -> None:
    assert has_obfuscated(REAL_OBFUSCATED_SALARY) is True
    assert has_obfuscated("20-30K·14薪") is False

    # 只删不猜：数字不会被凭空补出来
    stripped = strip_obfuscated(REAL_OBFUSCATED_SALARY)
    assert stripped == "-K·薪"
    assert "20" not in stripped

    assert describe_obfuscation(REAL_OBFUSCATED_SALARY)
    assert describe_obfuscation("20-30K") == ""


def test_parse_salary_refuses_obfuscated_text_explicitly() -> None:
    """混淆文本必须**显式**返回 None（我们知道自己不知道），而不是靠正则碰巧匹配不上。"""
    assert parse_salary(REAL_OBFUSCATED_SALARY) is None
    # 清掉私用区字符后依然解析不出 → 说明"清理"不等于"解码"，不能因此以为规则生效了
    assert parse_salary(strip_obfuscated(REAL_OBFUSCATED_SALARY)) is None
    # 正常文本不受影响
    assert parse_salary("20-30K·14薪") == (20000.0, 30000.0)


# --------------------------------------------------------------------------- #
# 5. 招呼语失败必须可见（端到端，走 notes）
# --------------------------------------------------------------------------- #
class _FailingLLM(LLMClient):
    name = "failing"

    async def complete(self, *, task: str, messages: list[LLMMessage], **kwargs: Any) -> LLMResult:
        raise RuntimeError("模型服务 503")


class _ObfuscatedSalaryAdapter:
    """只抓一张卡片的假适配器。

    **刻意模拟适配器的真实行为**：清理掉 PUA 码点后只留下 `-K·薪`，
    另用 `salary_obfuscated=True` 把"被混淆过"带出来——如果只看文本，
    这个信息在清理后就永久丢失了（第一版 runner 就是这么漏掉提示的）。
    """

    name = "fake"

    async def search(self, query: SearchQuery) -> list[JobCard]:
        return [
            JobCard(
                external_id="x-1",
                company="某某科技",
                title="视觉工程师",
                salary_range=strip_obfuscated(REAL_OBFUSCATED_SALARY),
                salary_obfuscated=has_obfuscated(REAL_OBFUSCATED_SALARY),
                city="北京",
                jd_text="负责视觉检测算法开发，熟悉 Halcon。",
            )
        ]

    async def fetch_detail(self, card: JobCard) -> JobCard:
        return card

    async def ensure_login(self) -> bool:
        return True

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None):  # type: ignore[no-untyped-def]
        raise AssertionError("本测试不应该投递")

    async def close(self) -> None:
        return None


def _settings() -> ApplySettings:
    return ApplySettings(
        enabled=True,
        risk_acknowledged=True,
        platform="mock",
        throttle=ThrottleConfig(per_minute=5, per_day=30, jitter_min_seconds=0.0, jitter_max_seconds=0.0),
    )


def _run(llm: LLMClient, adapter: Any) -> Any:
    async def _go() -> Any:
        with session_scope() as db:
            return await search_and_queue(
                db,
                adapter=adapter,
                query=SearchQuery(keywords=[], limit=5),
                # 只留"已投过去重"以外的规则都不开：本测试只想看 notes
                rules=[FilterRule(kind="dedup", priority=20, name="去重")],
                resume_text="四年机器视觉经验，熟悉 Halcon。",
                llm=llm,
                settings=_settings(),
            )

    return asyncio.run(_go())


def test_say_hi_failure_is_reported_in_notes(client: Any) -> None:
    outcome = _run(_FailingLLM(), _ObfuscatedSalaryAdapter())

    assert outcome.queued == 1
    joined = " ".join(outcome.notes)
    assert "招呼语生成失败" in joined
    assert "503" in joined  # 具体原因也要带出来，否则用户没法排查
    # 退化的模板话仍然可用（不会让整个候选生成失败）
    assert outcome.candidates[0].say_hi


def test_obfuscated_salary_is_reported_in_notes(client: Any) -> None:
    outcome = _run(_FailingLLM(), _ObfuscatedSalaryAdapter())

    joined = " ".join(outcome.notes)
    assert "薪资被平台用自定义字体混淆" in joined
    assert "「薪资区间」过滤规则对它们不生效" in joined


# --------------------------------------------------------------------------- #
# 6. 事件循环隔离（线上那个 500 的根因）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 的 SelectorEventLoop 不支持子进程")
def test_browser_call_is_isolated_from_selector_loop() -> None:
    """复现 `POST /api/apply/search` 的 500：在 SelectorEventLoop 上创建子进程会
    `NotImplementedError`，隔离之后必须落到一个 Proactor 循环上执行。

    这条测试是"事件循环隔离"能不能救回 Boss 抓候选的唯一证据——`uvicorn --reload`
    在 Windows 上就是 SelectorEventLoop，而 `scripts/dev.ps1` 永远带 `--reload`。
    """
    adapter = BossAdapter(user_data_dir="unused", selectors={})

    async def create_subprocess_like_playwright() -> str:
        # 这就是 patchright 启动浏览器做的事
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await proc.wait()
        return type(asyncio.get_running_loop()).__name__

    async def run_on_selector_loop() -> tuple[str, str]:
        ran_on = await adapter._run_browser_call(create_subprocess_like_playwright())
        return ran_on, type(asyncio.get_running_loop()).__name__

    loop = asyncio.SelectorEventLoop()
    try:
        ran_on, main_loop = loop.run_until_complete(run_on_selector_loop())
    finally:
        loop.close()
        adapter._shutdown_browser_loop()

    assert "Selector" in main_loop  # 模拟 uvicorn --reload（Windows 上是 _WindowsSelectorEventLoop）
    assert "Proactor" in ran_on  # 子进程是在隔离的 Proactor 循环里建的


@pytest.mark.skipif(sys.platform != "win32", reason="只在 Windows 上需要隔离")
def test_no_extra_thread_when_loop_already_supports_subprocess() -> None:
    """本来就是 Proactor 循环时不要多开线程（本地脚本/无 --reload 的路径保持原样）。"""
    adapter = BossAdapter(user_data_dir="unused", selectors={})

    async def trivial() -> str:
        return type(asyncio.get_running_loop()).__name__

    loop = asyncio.ProactorEventLoop()
    try:
        ran_on = loop.run_until_complete(adapter._run_browser_call(trivial()))
    finally:
        loop.close()

    assert "Proactor" in ran_on
    assert adapter._loop is None  # 没有创建独立循环
    assert adapter._loop_thread is None


# --------------------------------------------------------------------------- #
# 7. profile 被占用时必须给出人能照做的错误（而不是 500 + traceback）
# --------------------------------------------------------------------------- #
def test_profile_busy_launch_error_is_translated() -> None:
    """复现线上那个 500：Chrome 说「已在现有的浏览器会话中打开。」然后自己退出，
    Playwright 只报 `TargetClosedError: Target page, context or browser has been closed`。"""
    from app.apply.adapters.browser import _launch_error

    raw = (
        "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
        "Browser logs:\n\n<launching> chrome.exe --user-data-dir=... --remote-debugging-pipe about:blank\n"
        "<launched> pid=36372\n[pid=36372][out] 已在现有的浏览器会话中打开。\n"
    )
    error = _launch_error(RuntimeError(raw), r"D:\data\browser\boss", "Boss 直聘")

    message = str(error)
    assert "占用" in message
    assert r"D:\data\browser\boss" in message
    assert "关掉" in message
    assert isinstance(error, ApplyAdapterError)


def test_english_profile_busy_marker_is_also_translated() -> None:
    from app.apply.adapters.browser import _launch_error

    error = _launch_error(RuntimeError("Opened in existing browser session."), "/tmp/profile", "Boss")
    assert "占用" in str(error)


def test_unknown_launch_error_keeps_type_name() -> None:
    from app.apply.adapters.browser import _launch_error

    error = _launch_error(ValueError("chrome 没装"), "/tmp/profile", "Boss 直聘")
    assert "ValueError" in str(error)
    assert "Boss 直聘启动浏览器失败" in str(error)


# --------------------------------------------------------------------------- #
# 8. 城市必须持久化（不然每次抓取都要记得填，忘了就抓到别的城市）
# --------------------------------------------------------------------------- #
def test_city_survives_settings_round_trip(client: Any) -> None:
    """`save_settings` 是手写白名单——漏掉一个字段就会**静默重置**，
    实测就是这么把"许昌"记进 profile 的（城市没被保存 → 每次都不带城市）。"""
    from app.apply.runner import load_settings, save_settings

    with session_scope() as db:
        save_settings(db, ApplySettings(enabled=True, risk_acknowledged=True, platform="boss", city="101010100"))
    with session_scope() as db:
        restored = load_settings(db)
    assert restored.city == "101010100"
    assert restored.platform == "boss"

    # 清干净，避免影响其它用例
    with session_scope() as db:
        save_settings(db, ApplySettings())


def test_city_is_exposed_by_settings_api(client: Any) -> None:
    response = client.put(
        "/api/apply/settings",
        json={"enabled": True, "risk_acknowledged": True, "platform": "mock", "city": "101020100"},
    )
    assert response.status_code == 200
    assert response.json()["city"] == "101020100"

    assert client.get("/api/apply/settings").json()["city"] == "101020100"


def test_city_can_be_cleared_again(client: Any) -> None:
    """空城市是有意义的状态（＝用平台当前城市），必须能设回去。

    这里踩过：`city=payload.city or current.city` 会让空值变成"保持原值"，
    城市一旦设上就再也清不掉。所以它要跟 `enabled` / `want_salary_min` 一样按请求体重置。
    """
    base = {"enabled": True, "risk_acknowledged": True, "platform": "mock"}
    client.put("/api/apply/settings", json={**base, "city": "101010100"})
    assert client.get("/api/apply/settings").json()["city"] == "101010100"

    client.put("/api/apply/settings", json={**base, "city": ""})
    assert client.get("/api/apply/settings").json()["city"] == ""


def test_payload_city_overrides_setting_city(client: Any) -> None:
    """单次搜索可以覆盖设置里的城市：这里只验证接口接受该字段且设置不被改写。"""
    client.put(
        "/api/apply/settings",
        json={"enabled": True, "risk_acknowledged": True, "platform": "mock", "city": "101010100"},
    )
    response = client.post(
        "/api/apply/search",
        json={"keywords": ["后端"], "limit": 2, "city": "101020100"},
    )
    assert response.status_code == 200, response.text
    # 搜索用的城市是"当次覆盖"，不应该把设置改掉
    assert client.get("/api/apply/settings").json()["city"] == "101010100"


# --------------------------------------------------------------------------- #
# 9. 城市可以填名字（Boss 的 URL 只认代码，填名字会被静默换成别的城市）
# --------------------------------------------------------------------------- #
def test_city_name_is_translated_to_code() -> None:
    from app.apply.cities import city_code

    assert city_code("北京") == "101010100"
    assert city_code("北京市") == "101010100"  # 带后缀
    assert city_code("许昌") == "101180400"
    assert city_code("全国") == "100010000"
    assert city_code(" 上海 ") == "101020100"  # 两边空格
    assert city_code("101010100") == "101010100"  # 直接填代码
    assert city_code("") == ""  # 空＝用平台当前城市
    assert city_code(None) == ""


def test_unknown_city_is_rejected_not_guessed() -> None:
    """认不出来必须报错：Boss 对非法城市是"静默换一个城市继续搜"，猜错的代价是整批岗位跑偏。"""
    import pytest as _pytest

    from app.apply.cities import city_code

    with _pytest.raises(ValueError) as err:
        city_code("某某不存在市")
    assert "认不出城市" in str(err.value)


def test_city_name_accepted_by_settings_api(client: Any) -> None:
    base = {"enabled": True, "risk_acknowledged": True, "platform": "mock"}
    saved = client.put("/api/apply/settings", json={**base, "city": "许昌"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["city"] == "101180400"  # 存的是代码

    bad = client.put("/api/apply/settings", json={**base, "city": "查无此城"})
    assert bad.status_code == 400
    assert "认不出城市" in bad.json()["detail"]


def test_detail_salary_rescues_obfuscated_list_salary() -> None:
    """实测：列表页薪资是私用区码点，**详情页同一字段是正常文本**（`12-18K·13薪`）。

    所以详情页取到值就必须用它——这直接决定"薪资区间"规则是否生效。
    """
    from app.apply.adapters.browser import merge_salary

    # 列表页被混淆 + 详情页正常 → 用详情页，并且标记为"未混淆"
    value, obfuscated = merge_salary(strip_obfuscated(REAL_OBFUSCATED_SALARY), True, "12-18K·13薪")
    assert (value, obfuscated) == ("12-18K·13薪", False)
    # 于是薪资规则真的能工作了
    assert parse_salary(value) == (12000.0, 18000.0)

    # 详情页没取到 → 保留列表页那份（含标记），不要假装有薪资
    assert merge_salary("-K·薪", True, "") == ("-K·薪", True)

    # 详情页也被混淆 → 清理后保留，并继续标记为未解析
    value, obfuscated = merge_salary("-K", True, REAL_OBFUSCATED_SALARY)
    assert value == "-K·薪"
    assert obfuscated is True


def test_obfuscated_list_salary_alone_never_parses() -> None:
    """只有列表页时，薪资规则必须"什么都拦不了"——这就是之前凑数岗位漏进来的原因。"""
    from app.apply.filters import _salary_conflict, JobFacts

    facts = JobFacts(company="某公司", title="某岗位", salary_range=strip_obfuscated(REAL_OBFUSCATED_SALARY))
    reason = _salary_conflict(facts, {"min_salary": 20000, "max_salary": 80000})
    assert reason is None  # 不拦，也不误拦


class _BrokenDetailAdapter:
    """列表页正常，但读详情页时抛 ApplyAdapterError（真机踩过：选择器命中多个元素）。"""

    name = "broken-detail"

    async def search(self, query: SearchQuery) -> list[JobCard]:
        return [
            JobCard(external_id="d-1", company="创想智控", title="视觉工程师", salary_range="-K·薪",
                    salary_obfuscated=True, jd_text=""),
            JobCard(external_id="d-2", company="某某科技", title="算法工程师", salary_range="-K",
                    salary_obfuscated=True, jd_text=""),
        ]

    async def fetch_detail(self, card: JobCard) -> JobCard:
        from app.apply.adapters.base import ApplyAdapterError as _Err

        raise _Err("读取详情页字段失败（Error）：strict mode violation: locator(\"div.job-sec-text\") resolved to 3 elements")

    async def ensure_login(self) -> bool:
        return True

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None):  # type: ignore[no-untyped-def]
        raise AssertionError("本测试不应该投递")

    async def close(self) -> None:
        return None


def test_detail_failure_does_not_break_the_whole_search(client: Any) -> None:
    """真机现场：`div.job-sec-text` 命中 3 个元素 → strict 模式抛错 → 整个
    `POST /api/apply/search` 500、一行岗位都没入库。

    期望行为：详情页读不了只是"我们自己的适配问题"——记一条 note 并继续下一个岗位，
    绝不把整轮抓取弄崩（熔断器也只该停本轮、不停当天）。
    """

    async def _go() -> Any:
        with session_scope() as db:
            return await search_and_queue(
                db,
                adapter=_BrokenDetailAdapter(),
                query=SearchQuery(keywords=[], limit=5),
                rules=[FilterRule(kind="dedup", priority=20, name="去重")],
                resume_text="四年机器视觉经验。",
                llm=_FailingLLM(),
                settings=_settings(),
                fetch_detail=True,
            )

    outcome = asyncio.run(_go())

    # 关键：没有抛出去，而且每个岗位都被尝试过并留下了原因
    assert outcome.fetched == 2
    notes = " ".join(outcome.notes)
    assert "抓详情失败" in notes
    assert "strict mode violation" in notes  # 原始原因要带出来，否则没法排查
    assert outcome.queued == 0  # 详情没读到 → 不生成候选，但整轮没崩


# --------------------------------------------------------------------------- #
# 10. HR 活跃：**没有标记 ≠ 不活跃**（用户截图里一片"HR 长期不活跃"的根因）
# --------------------------------------------------------------------------- #
def test_hr_active_status_text_is_judged_not_guessed() -> None:
    from app.apply.adapters.browser import hr_active_from_status

    # 最近活跃的各种说法
    for text in ("在线", "刚刚活跃", "本周活跃", "今日活跃", "3日内活跃", "本月活跃", "1日内活跃"):
        assert hr_active_from_status(text) is True, text

    # 明确长期不活跃
    for text in ("半年前活跃", "年前活跃", "3月前活跃", "6月前活跃", "长期未活跃"):
        assert hr_active_from_status(text) is False, text

    # 关键：什么标记都没有时是"不知道"，**不能当成不活跃**（否则几乎每个岗位都被拦）
    assert hr_active_from_status("") is None
    assert hr_active_from_status("   ") is None
    # 认不出来的说法也不猜
    assert hr_active_from_status("招聘者") is None


def test_hr_active_rule_only_blocks_known_inactive() -> None:
    """"未知"不该被拦——这条测试固定住那个把用户淹掉的误判。"""
    from app.apply.filters import FilterRule, JobFacts, evaluate

    rule = FilterRule(kind="hr_active", params={"require_active": True}, name="HR 需活跃")

    unknown = evaluate(JobFacts(company="A", title="视觉工程师", hr_active=None), [rule])
    assert unknown.reasons == []  # 不知道 → 放行

    inactive = evaluate(JobFacts(company="A", title="视觉工程师", hr_active=False), [rule])
    assert "HR 长期不活跃" in " ".join(inactive.reasons)

    active = evaluate(JobFacts(company="A", title="视觉工程师", hr_active=True), [rule])
    assert active.reasons == []


# --------------------------------------------------------------------------- #
# 11. 「期望月薪下限」必须真的影响拦截（它以前只管打分）
# --------------------------------------------------------------------------- #
def test_want_salary_min_is_synced_into_salary_rule(client: Any) -> None:
    """实测用户把下限填成 8000，规则里还是默认的 20000-80000 → 10-15K 照样被拦，
    拦截理由还写着"与期望 20000-80000 无交集"，用户完全无从理解。"""
    base = {"enabled": True, "risk_acknowledged": True, "platform": "mock"}

    client.put("/api/apply/settings", json={**base, "want_salary_min": 8000})
    rules = client.get("/api/apply/rules").json()
    salary_rule = next(r for r in rules if r["kind"] == "salary")
    assert salary_rule["params"]["min_salary"] == 8000

    # 下限为 0 视为"没意见"，不该把规则改回 0（否则用户没法只改上限）
    client.put("/api/apply/settings", json={**base, "want_salary_min": 0})
    rules = client.get("/api/apply/rules").json()
    assert next(r for r in rules if r["kind"] == "salary")["params"]["min_salary"] == 8000


# --------------------------------------------------------------------------- #
# 12. 静默模式必须真的生效（"设置里选了但没传到浏览器"是最难发现的一类 bug）
# --------------------------------------------------------------------------- #
def test_window_mode_is_wired_into_browser_launch() -> None:
    from app.api.apply import _build_adapter

    offscreen = _build_adapter(ApplySettings(platform="boss", window_mode="offscreen"))
    assert offscreen._headless is False  # 仍是"有头"的真实 Chrome
    assert any("window-position" in arg for arg in offscreen._extra_args)

    headless = _build_adapter(ApplySettings(platform="boss", window_mode="headless"))
    assert headless._headless is True
    assert headless._extra_args == []

    visible = _build_adapter(ApplySettings(platform="boss", window_mode="visible"))
    assert visible._headless is False
    assert visible._extra_args == []


def test_window_mode_is_persisted_and_rejects_garbage(client: Any) -> None:
    base = {"enabled": True, "risk_acknowledged": True, "platform": "mock"}

    saved = client.put("/api/apply/settings", json={**base, "window_mode": "offscreen"})
    assert saved.json()["window_mode"] == "offscreen"
    assert client.get("/api/apply/settings").json()["window_mode"] == "offscreen"

    # 前端传了不认识的值时保留原值，别把配置写坏
    bad = client.put("/api/apply/settings", json={**base, "window_mode": "teleport"})
    assert bad.json()["window_mode"] == "offscreen"


# --------------------------------------------------------------------------- #
# 13. 投递必须先导航到岗位页（真机现场：第一条 30 秒超时、第二条会发错人）
# --------------------------------------------------------------------------- #
class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    async def count(self) -> int:
        return 0  # 风控标志都不存在

    async def inner_text(self) -> str:
        return ""

    @property
    def first(self) -> "_FakeLocator":
        return self


class _FakePage:
    """记录调用的假页面：能验证"顺序"（先导航、再点击），这是本 bug 的核心。"""

    def __init__(self, name: str, log: list[tuple[str, str]], *, closed: bool = False) -> None:
        self.name = name
        self.url = "about:blank"
        self._log = log
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def goto(self, url: str, **kwargs: object) -> None:
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        self._log.append(("goto", url))
        self.url = url

    async def click(self, selector: str, **kwargs: object) -> None:
        if self._closed:
            raise RuntimeError("Target page, context or browser has been closed")
        self._log.append((f"click:{self.name}", selector))

    async def type(self, selector: str, text: str, **kwargs: object) -> None:
        self._log.append((f"type:{self.name}", selector))


class _FakeContext:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages


def _fake_adapter(pages: list[_FakePage], log: list[tuple[str, str]]) -> Any:
    from app.apply.adapters.boss import BossAdapter

    # 必须用 BossAdapter（而不是裸 BrowserAdapter）：选择器集合是按平台名加载的，
    # 裸 BrowserAdapter 拿到的是没有 greet_* 字段的通用集合
    adapter = BossAdapter(
        user_data_dir="unused",
        selectors={
            "greet_button": "a.btn.btn-startchat",
            "greet_input": "#chat-input",
            "greet_send": "button[type=send]",
        },
    )
    adapter._page = pages[0]
    adapter._context = _FakeContext(pages)
    adapter._pause = lambda window: asyncio.sleep(0)  # type: ignore[method-assign]
    adapter._type_like_human = _make_typer(pages[0], log)  # type: ignore[method-assign]
    return adapter


def _make_typer(page: _FakePage, log: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    async def _type(selector: str, text: str) -> None:
        log.append((f"type:{page.name}", selector))

    return _type


def test_send_greeting_navigates_to_the_job_page_first(client: Any) -> None:
    """真机现场：`/execute` 是一个**新的浏览器会话**，页面停在 about:blank，
    不导航就点「立即沟通」→ 30 秒超时；而第二条任务时页面还停在上一条岗位 →
    招呼语会发给**上一个 HR**（更严重）。这条测试把"先导航"钉死。"""
    log: list[tuple[str, str]] = []
    page = _FakePage("main", log)
    adapter = _fake_adapter([page], log)
    card = JobCard(external_id="j-1", company="聚贤自动化", title="机器人视觉工程师",
                   jd_url="https://www.zhipin.com/job_detail/abc.html")

    result = asyncio.run(adapter.send_greeting(card, "你好，我做机器视觉四年"))

    assert result.ok is True, result.detail
    kinds = [entry[0] for entry in log]
    assert kinds[0] == "goto", f"第一次操作必须是导航到岗位页：{log}"
    assert log[0][1] == card.jd_url
    assert kinds[1].startswith("click")
    assert log[1][1] == "a.btn.btn-startchat"
    assert "type:main" in kinds[2]
    assert log[2][1] == "#chat-input"
    assert log[3][1] == "button[type=send]"


def test_send_greeting_switches_to_a_newly_opened_chat_tab(client: Any) -> None:
    """点「立即沟通」有的平台会**新开标签页**：输入框在新页上，还盯着旧页会等不到元素。"""
    log: list[tuple[str, str]] = []
    main = _FakePage("main", log)
    chat = _FakePage("chat", log)
    adapter = _fake_adapter([main], log)
    card = JobCard(external_id="j-2", company="某公司", title="视觉工程师",
                   jd_url="https://www.zhipin.com/job_detail/xyz.html")

    async def _go() -> Any:
        # 点「立即沟通」时把聊天页塞进 context，模拟新开标签页
        original_click = main.click

        async def click_and_open(selector: str, **kwargs: object) -> None:
            await original_click(selector, **kwargs)
            adapter._context.pages.append(chat)
            adapter._type_like_human = _make_typer(chat, log)  # type: ignore[method-assign]

        main.click = click_and_open  # type: ignore[method-assign]
        return await adapter.send_greeting(card, "你好")

    result = asyncio.run(_go())

    assert result.ok is True, result.detail
    # 输入与发送都发生在**新开的聊天页**上，而不是旧页
    assert ("type:chat", "#chat-input") in log
    assert ("click:chat", "button[type=send]") in log


def test_browser_closed_midway_is_explained_not_just_crashed(client: Any) -> None:
    """实测：后端 `--reload` 时改代码 → worker 重启 → 浏览器被杀，
    报错只有一句 `Target page, context or browser has been closed`。这必须翻译成
    "本轮没发出去、重新点一次"这种能照做的说明，并且**不能熔断当天**（page_changed）。"""
    log: list[tuple[str, str]] = []
    page = _FakePage("main", log, closed=True)
    adapter = _fake_adapter([page], log)
    card = JobCard(external_id="j-3", company="某公司", title="视觉工程师",
                   jd_url="https://www.zhipin.com/job_detail/dead.html")

    result = asyncio.run(adapter.send_greeting(card, "你好"))

    assert result.ok is False
    assert "浏览器在操作过程中被关闭" in result.detail
    assert "没有发送任何消息" in result.detail
    assert result.signal is not None
    assert result.signal.kind == "page_changed"  # 只停本轮，不停当天
    assert not any(entry[0].startswith("type") for entry in log)  # 什么都没发出去


def test_failed_task_can_be_re_approved(client: Any) -> None:
    """失败的任务以前是死路（只能重抓岗位，而重抓会撞上"已投过去重"）。"""
    from app.apply.runner import approve_tasks, skip_tasks
    from app.models.apply import ApplyTask, JobPosting

    with session_scope() as db:
        posting = JobPosting(platform="mock", external_id="re-1", company="某公司", title="视觉工程师")
        db.add(posting)
        db.commit()
        db.refresh(posting)
        task = ApplyTask(job_posting_id=posting.id, status="failed", say_hi_text="你好", error="旧的报错")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = int(task.id or 0)

    with session_scope() as db:
        assert approve_tasks(db, task_ids=[task_id]) == 1
    with session_scope() as db:
        again = db.get(ApplyTask, task_id)
        assert again is not None
        assert again.status == "approved"
        assert again.error == ""  # 旧报错要清掉

    # 收尾：别把这条留在库里影响别的用例
    with session_scope() as db:
        skip_tasks(db, task_ids=[task_id])


class _RaisingAdapter:
    """发送时抛**非** ApplyAdapterError 的适配器（真机现场：浏览器被关闭）。"""

    name = "raising"

    async def search(self, query: SearchQuery) -> list[JobCard]:
        return []

    async def fetch_detail(self, card: JobCard) -> JobCard:
        return card

    async def ensure_login(self) -> bool:
        return True

    async def send_greeting(self, card: JobCard, text: str, resume_image: str | None = None):  # type: ignore[no-untyped-def]
        raise RuntimeError("Page.click: Target page, context or browser has been closed")

    async def close(self) -> None:
        return None


def test_execute_survives_an_adapter_exception(client: Any) -> None:
    """单个任务抛异常不能把整轮执行打成 500：要落成"这条失败 + 本轮停止"，
    并且剩下的已确认任务仍然是 approved（可以重试），而不是被默默丢掉。"""
    from app.apply.runner import execute_approved, load_settings, save_settings
    from app.models.apply import ApplyTask, JobPosting

    with session_scope() as db:
        save_settings(db, ApplySettings(enabled=True, risk_acknowledged=True, platform="mock"))
        for index in range(2):
            posting = JobPosting(platform="mock", external_id=f"raise-{index}",
                                 company=f"某公司{index}", title="视觉工程师",
                                 jd_url=f"https://www.zhipin.com/job_detail/{index}.html")
            db.add(posting)
            db.commit()
            db.refresh(posting)
            db.add(ApplyTask(job_posting_id=posting.id, status="approved", say_hi_text="你好"))
        db.commit()
        settings = load_settings(db)

    async def _go() -> Any:
        with session_scope() as db:
            return await execute_approved(db, adapter=_RaisingAdapter(), settings=settings, limit=10)

    outcome = asyncio.run(_go())

    assert outcome.attempted == 1  # 第一条就出错，本轮停止
    assert outcome.failed == 1
    assert "has been closed" in outcome.stopped_reason
    assert outcome.items[0].ok is False

    # 收尾：清掉这批测试数据
    from app.apply.runner import skip_tasks

    with session_scope() as db:
        from sqlmodel import select as _select

        ids = [int(t.id or 0) for t in db.exec(_select(ApplyTask)).all()
               if t.status in {"approved", "failed"}]
        skip_tasks(db, task_ids=ids)
        save_settings(db, ApplySettings())


# --------------------------------------------------------------------------- #
# 14. 队列里的"删除"与"拉黑"（用户要的是"这些公司别出现在队列里"）
# --------------------------------------------------------------------------- #
def _seed_queue_tasks(count: int = 2) -> list[tuple[int, str]]:
    from app.models.apply import ApplyTask, JobPosting

    created: list[tuple[int, str]] = []
    with session_scope() as db:
        for index in range(count):
            company = f"待清理公司{index}"
            posting = JobPosting(platform="boss", external_id=f"clean-{index}", company=company,
                                 title="视觉工程师", jd_url=f"https://example.com/{index}")
            db.add(posting)
            db.commit()
            db.refresh(posting)
            task = ApplyTask(job_posting_id=posting.id, status="pending", say_hi_text="你好")
            db.add(task)
            db.commit()
            db.refresh(task)
            created.append((int(task.id or 0), company))
    return created


def test_delete_queue_tasks_removes_them_for_real(client: Any) -> None:
    """删除必须是真删（不是标成 skipped）——用户要的是"这一行别再出现"。"""
    from app.apply.runner import delete_tasks
    from app.models.apply import ApplyTask

    ids = [task_id for task_id, _ in _seed_queue_tasks(2)]

    with session_scope() as db:
        assert delete_tasks(db, task_ids=ids) == 2
    with session_scope() as db:
        assert all(db.get(ApplyTask, task_id) is None for task_id in ids)
        # 再删一次不该报错也不该计数
        assert delete_tasks(db, task_ids=ids) == 0


def test_blacklist_and_remove_blacklists_deduped_and_removes(client: Any) -> None:
    from app.apply.runner import blacklist_and_remove, delete_tasks
    from app.models.apply import ApplyTask, Blacklist
    from sqlmodel import select as _select

    seeded = _seed_queue_tasks(2)
    ids = [task_id for task_id, _ in seeded]
    companies = [company for _, company in seeded]

    with session_scope() as db:
        added, removed = blacklist_and_remove(db, task_ids=ids)
    assert (added, removed) == (2, 2)

    with session_scope() as db:
        rows = [row.company for row in db.exec(_select(Blacklist)).all() if row.company in companies]
        assert sorted(rows) == sorted(companies)
        assert all(db.get(ApplyTask, task_id) is None for task_id in ids)

        # 同一家公司再加一次不该重复
        from app.apply.runner import blacklist_companies

        assert blacklist_companies(db, companies=companies, platform="boss") == 0

    # 收尾：清掉这些黑名单行，免得影响其它用例
    with session_scope() as db:
        for row in db.exec(_select(Blacklist)).all():
            if row.company in companies:
                db.delete(row)
        db.commit()
        delete_tasks(db, task_ids=ids)


def test_blacklisted_company_is_blocked_by_the_rule(client: Any) -> None:
    """拉黑的意义在于**下次抓取时自动拦下**——这条把整条链路串起来验证。

    用 Mock 适配器里真实存在的公司名（某某科技），这样"黑名单集合 → 过滤链"这条路
    是真的被走到的，而不是用一个人造公司名假装通过。
    """
    from app.apply.adapters import MockPlatformAdapter
    from app.apply.filters import FilterRule, JobFacts, evaluate
    from app.apply.runner import blacklist_companies
    from app.models.apply import Blacklist
    from sqlmodel import select as _select

    company = "某某科技"  # MockPlatformAdapter 的默认岗位之一
    with session_scope() as db:
        assert blacklist_companies(db, companies=[company], platform="mock") == 1

    try:
        rule = FilterRule(kind="blacklist", priority=10, name="黑名单公司")

        # 过滤层：黑名单判定直接看 facts.blacklisted（由 runner 从库里喂进去）
        blocked = evaluate(JobFacts(company=company, title="视觉工程师", blacklisted=True), [rule])
        assert blocked.reasons and "黑名单" in blocked.reasons[0]
        assert not blocked.passed

        # 端到端：抓一轮，这家公司的岗位必须被拦下并写明原因
        async def _search() -> Any:
            with session_scope() as db:
                return await search_and_queue(
                    db,
                    adapter=MockPlatformAdapter(),
                    query=SearchQuery(keywords=[], limit=10),
                    rules=[rule],
                    resume_text="",
                    llm=_FailingLLM(),
                    settings=_settings(),
                )

        outcome = asyncio.run(_search())
        assert outcome.blocked >= 1
        blocked_companies = [
            item.company for item in outcome.candidates if not item.passed
        ]
        assert company in blocked_companies
        assert any("黑名单" in " ".join(item.reasons) for item in outcome.candidates if not item.passed)
    finally:
        with session_scope() as db:
            for row in db.exec(_select(Blacklist)).all():
                if row.company == company:
                    db.delete(row)
            db.commit()


def test_notes_are_empty_for_clean_data(client: Any) -> None:
    """没有问题的数据不该产生噪音提示。"""
    from app.apply.adapters import MockPlatformAdapter

    outcome = _run(_FailingLLM(), MockPlatformAdapter(cards=[
        JobCard(
            external_id="ok-1",
            company="某某科技",
            title="后端工程师",
            salary_range="25-40K",
            city="杭州",
            jd_text="Python 后端，熟悉 Redis 与 MySQL。",
        )
    ]))

    assert outcome.queued == 1
    assert [note for note in outcome.notes if "薪资" in note] == []
