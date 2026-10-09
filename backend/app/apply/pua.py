"""平台用自定义字体把数字换成私有区码点（PUA）时的识别与清理。

**问题**：Boss 直聘的薪资在 DOM 里不是 `20-30K·14薪`，而是一串私用区字符。实测抓到的
`span.job-salary` 内容是：

    U+E032 U+E033 - U+E032 U+E039 K · U+E032 U+E034 薪

终端里这些码点不显示，看起来就是 `-K·薪`，于是"薪资看着有值、其实没有"。

**为什么不直接解码**：参考项目 get_jobs 用一张写死的映射表（`U+E8F0..U+E8F9` → `0..9`）。
但实测 Boss 的映射**会轮换**——我们这次碰到的是 `U+E032..U+E039`，跟那张表完全不在一个区间。
写死的表一旦轮换，会把数字**解错**（而不是解不出）；在"投递决策"这种错了要花钱的场景里，
解错比不上解不出。所以这里只做三件事：

1. `has_obfuscated`：**识别**这段文本被混淆过；
2. `strip_obfuscated`：清掉不可见码点，让 UI 显示成 `-K·薪` 而不是一串空白（诚实但难看，
   比假装有数字强）；
3. 把"未解析"这件事**报给用户**（由调用方放进 notes），而不是静默按"没有薪资"处理。

真要拿到准确薪资，两条已验证的路子（见 get_jobs）：
- 拦截平台自己的 JSON 接口（Boss 是 `/wapi/zpgeek/job/detail.json` 里的 `salaryDesc`，干净文本）；
- 或者用平台 UI 的薪资筛选，让平台自己过滤，根本不需要解析这段文本。
"""

from __future__ import annotations

#: Unicode 私用区（PUA）：基本多文种平面 + 两个补充平面
_PUA_RANGES = (
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)


def is_pua(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _PUA_RANGES)


def has_obfuscated(text: str | None) -> bool:
    """文本里是否含私用区字符（= 平台用了字体反爬）。"""
    return any(is_pua(ch) for ch in (text or ""))


def strip_obfuscated(text: str | None) -> str:
    """去掉私用区字符，保留其余可读部分（并压掉因此产生的多余空格）。

    只删不猜：`U+E032 U+E033 - ...` 会变成 `- ...`，而不是被猜成 `20- ...`。
    """
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if not is_pua(ch))
    # 私用区字符常被当成"数字"占位，删掉后会留下多余空格（"  -K" → "-K"）
    return " ".join(cleaned.split()).strip() if cleaned.strip() else cleaned.strip()


def describe_obfuscation(text: str | None) -> str:
    """给用户看的一句话（不猜数字，只说事实）。"""
    if not has_obfuscated(text):
        return ""
    return "薪资数字被平台用自定义字体替换（私用区码点），无法解析成数值"
