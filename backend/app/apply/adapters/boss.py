"""Boss 直聘适配器（patchright 驱动真实 Chrome）。

⚠️ **诚实声明**：选择器没有在真实页面上校准过（开发机没有浏览器自动化环境）。
所以本实现的策略是"**选择器全部外置**"，未配置时明确报错，而不是瞎猜——
编一套看起来能用、实际第一次跑就崩的选择器，比先留空更糟。

M14 起，通用逻辑（启动、节奏、风控信号、抓卡片/详情/发招呼）搬到了 `BrowserAdapter`，
这个文件只负责 Boss 自己的**地址**。接第二个平台（猎聘）时只加了一个同样短的 `liepin.py`。

已经真实可用的部分（都在基类里）：
- 真实 Chrome（`channel="chrome"`）+ 持久化上下文 + 有头模式；
- 人类化节奏：随机延迟、逐字输入、点击前后的停顿；
- 登录态持久化；
- 登录失效/验证码的**信号识别**（交给熔断器决定当天是否停投）。
"""

from __future__ import annotations

from .browser import BrowserAdapter

#: `{keyword}` 与 `{city}` 会被替换；城市为空时城市参数留空
BOSS_HOME = "https://www.zhipin.com/web/geek/job?query={keyword}&city={city}"


class BossAdapter(BrowserAdapter):
    name = "boss"
    adapter_label = "Boss 直聘"
    home_url = BOSS_HOME
    docs_hint = "docs/APPLY_SELECTORS.md"
