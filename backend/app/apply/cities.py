"""城市：名字 ↔ Boss 城市代码。

**为什么需要它**：Boss 的搜索 URL 只认城市**代码**（`&city=101010100`）。填中文名字会被
平台当成非法值——而且不是报错，是**静默换成它自己挑的城市**（实测它会挑 IP 定位的城市，
并把那个城市记进 profile，之后每次不带城市都跟着跑偏）。所以这里必须把用户填的东西
转成代码，转不出来就**明确报错**，绝不把可疑的值放过去。

数据来源：Boss 自己的城市表 `https://www.zhipin.com/wapi/zpCommon/data/city.json`
（`zpData.hotCityList` + `zpData.cityList` 的二级节点，共 400 条），已落成同目录的
`city_codes.json`——**不在运行时联网**，免得抓个岗位还要多打一次平台的接口。
重新生成：`scripts/refresh_city_codes.py`。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).with_name("city_codes.json")

#: 名字里可以省略的后缀（"北京市" → "北京"）
_SUFFIXES = ("市", "特别行政区", "自治区", "自治州", "地区", "盟", "省")

#: 城市代码是纯数字（Boss 是 9 位，如 101010100；区级是 6 位）
_CODE_RE = re.compile(r"^\d{6,12}$")


@lru_cache(maxsize=1)
def city_map() -> dict[str, int]:
    """名字 → 代码。文件缺失时返回空表（调用方会因此拒绝中文名，而不是猜）。"""
    try:
        data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(name): int(code) for name, code in data.items() if str(name).strip()}


def city_code(value: str | None) -> str:
    """把用户填的城市转成 Boss 能用的代码。

    接受：空（返回 ""，表示"用平台当前城市"）、纯数字代码、中文名（"许昌" / "许昌市"）。
    不能识别时抛 `ValueError`——**宁可让用户改一个字，也不要静默搜错城市**。
    """
    text = (value or "").strip()
    if not text:
        return ""
    if _CODE_RE.match(text):
        return text

    table = city_map()
    if not table:
        raise ValueError(
            f"城市表缺失（{_DATA_FILE.name}），无法把「{text}」转成城市代码；"
            "请直接填 Boss 的城市代码（例如北京 101010100）"
        )

    for candidate in _name_variants(text):
        hit = table.get(candidate)
        if hit is not None:
            return str(hit)

    # 拼音首字母等模糊匹配不做：猜错城市比让用户改一个字代价大得多
    raise ValueError(
        f"认不出城市「{text}」。请填 Boss 的城市名（如 北京、许昌）或城市代码"
        f"（如北京 101010100、许昌 101180400）"
    )


def _name_variants(text: str) -> list[str]:
    """"许昌市" → ["许昌市", "许昌"]；"内蒙古自治区" → [..., "内蒙古"]。"""
    variants = [text]
    stripped = text
    for suffix in _SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
            if stripped and stripped not in variants:
                variants.append(stripped)
    return variants


def known_city_names(limit: int = 0) -> list[str]:
    """给 UI/文档看的一小撮样例（按名字排序）。"""
    names = sorted(city_map(), key=lambda name: (len(name), name))
    return names[:limit] if limit else names
