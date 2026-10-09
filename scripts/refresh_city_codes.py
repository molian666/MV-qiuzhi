"""重新生成 `backend/app/apply/city_codes.json`（城市名 → Boss 城市代码）。

数据来自 Boss 自己的城市表接口，**不在应用运行时联网**——抓岗位已经够惹风控了，
不该再为"城市名转代码"多打一次平台的接口。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\refresh_city_codes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

CITY_API = "https://www.zhipin.com/wapi/zpCommon/data/city.json"
OUT = Path(r"D:\VibeCoding\mianshi\backend\app\apply\city_codes.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.zhipin.com/",
}


def main() -> int:
    with httpx.Client(timeout=30.0, headers=HEADERS, trust_env=False) as client:
        response = client.get(CITY_API)
        response.raise_for_status()
        payload = response.json()

    zp = payload.get("zpData") or {}
    mapping: dict[str, int] = {}

    # 热门城市优先（"全国"只在这儿有）
    for item in zp.get("hotCityList") or []:
        if item.get("name") and item.get("code"):
            mapping.setdefault(str(item["name"]), int(item["code"]))

    # cityList 是"省 → 市 → 区"三级；只取到市级（区级不是搜索用的城市参数）
    def walk(nodes: list[dict] | None, level: int) -> None:
        for node in nodes or []:
            name, code = node.get("name"), node.get("code")
            if name and code and level <= 2:
                mapping.setdefault(str(name), int(code))
            if level < 2:
                walk(node.get("subLevelModelList"), level + 1)

    walk(zp.get("cityList"), 1)

    if len(mapping) < 100:
        print(f"城市表看起来不完整（只有 {len(mapping)} 条），拒绝覆盖")
        return 1

    OUT.write_text(json.dumps(mapping, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"已写入 {OUT}（{len(mapping)} 条，{OUT.stat().st_size} bytes）")
    print("抽样：", {name: mapping[name] for name in ("全国", "北京", "许昌", "郑州") if name in mapping})
    return 0


if __name__ == "__main__":
    sys.exit(main())
