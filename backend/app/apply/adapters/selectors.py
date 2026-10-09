"""平台选择器配置（外置，便于校准）。

为什么外置而不是写死在适配器里：
1. 平台改版时只需要改这份配置，业务代码一行不动；
2. **未校准时能明确报"缺哪个选择器"**，而不是抛一个看不懂的 NoneType 错误；
3. 不同账号/不同城市的页面结构偶有差异，用户能自己微调。

所以 `BossSelectors` 的字段默认全空，适配器在用到时检查并报错，
错误里直接带上字段名与校准文档的位置。
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(slots=True)
class SelectorSet:
    """一组选择器。字段名即"用途"，空字符串表示还没校准。"""

    def missing(self, *names: str) -> list[str]:
        return [name for name in names if not (getattr(self, name, "") or "").strip()]

    def to_dict(self) -> dict[str, str]:
        return {item.name: str(getattr(self, item.name) or "") for item in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, str] | None):
        payload = data or {}
        known = {item.name for item in fields(cls)}
        return cls(**{key: str(value) for key, value in payload.items() if key in known})


@dataclass(slots=True)
class BossSelectors(SelectorSet):
    """Boss 直聘。校准方法见 docs/APPLY_SELECTORS.md。"""

    search_input: str = ""
    search_button: str = ""
    city_selector: str = ""

    job_card: str = ""
    card_title: str = ""
    card_company: str = ""
    card_salary: str = ""
    card_link: str = ""
    card_city: str = ""

    detail_jd: str = ""
    detail_experience: str = ""
    detail_degree: str = ""
    #: 详情页的薪资。**列表页的薪资数字被平台用自定义字体混淆**（实测 DOM 里是私用区码点），
    #: 而详情页同一字段是正常文本（实测 `12-18K·13薪`），所以这里取到就能还原真实薪资。
    detail_salary: str = ""
    hr_name: str = ""
    hr_active: str = ""
    headhunter_marker: str = ""

    greet_button: str = ""
    greet_input: str = ""
    greet_send: str = ""

    #: 「投递简历」：`greet_button` 只是打开沟通，**简历要靠另外的动作发出去**。
    #: 下面四个是"真的把简历发过去"所需的选择器；未校准时附件投递会**明确报错**，
    #: 而不是安静地只发一句招呼语就让状态变成"已投"。
    resume_send_button: str = ""
    #: 那个 `input[type=file]`（平台上通常被 CSS 藏起来，所以用 `set_input_files`
    #: 直接设值，不去点它——点一个不可见元素会超时）
    resume_upload_input: str = ""
    resume_confirm_button: str = ""
    #: 点完发送后用来确认"真的发出去了"（出现即视为成功）
    resume_sent_marker: str = ""

    captcha_marker: str = ""
    login_marker: str = ""


@dataclass(slots=True)
class LiepinSelectors(BossSelectors):
    """猎聘。字段用途与 Boss 相同（都是"搜索框/卡片/详情/招呼"这几件事），
    所以直接复用同一套字段定义——**只有值不同**。多一层继承比复制一遍字段更不容易漏。"""

    #: 猎聘特有的"猎头标记"在列表页也有
    list_headhunter_marker: str = ""


SELECTOR_SETS: dict[str, type[SelectorSet]] = {
    "boss": BossSelectors,
    "liepin": LiepinSelectors,
}


def load_selectors(platform: str, data: dict[str, str] | None = None) -> SelectorSet:
    """按平台取选择器集合（未知平台给一个空的通用集合，适配器会报错提示）。"""
    factory = SELECTOR_SETS.get(platform, SelectorSet)
    return factory.from_dict(data)
