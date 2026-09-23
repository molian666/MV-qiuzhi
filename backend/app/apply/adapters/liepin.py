"""猎聘适配器（M14 接入的第二个投递平台）。

**这个文件本身就说明了抽象是否成立**：它只有地址和名字——
启动浏览器、人类化节奏、识别验证码、抓卡片/详情、发招呼语，全在 `BrowserAdapter` 里。
如果接一个平台要改上百行，那 M10 的平台抽象就是假的。

⚠️ 与 Boss 一样，**选择器需要在真实页面上校准**（`docs/APPLY_SELECTORS.md`）。
未校准时状态接口会明确报出缺哪些字段并拒绝投递。
"""

from __future__ import annotations

from .browser import BrowserAdapter

#: 猎聘的搜索结果页
LIEPIN_HOME = "https://www.liepin.com/zhaopin/?key={keyword}"


class LiepinAdapter(BrowserAdapter):
    name = "liepin"
    adapter_label = "猎聘"
    home_url = LIEPIN_HOME
    docs_hint = "docs/APPLY_SELECTORS.md"
