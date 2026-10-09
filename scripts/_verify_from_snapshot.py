"""用真机抓回来的 first_card_html 在本地做离线验证：完全照 browser.py 的取法抽一遍。

这样不需要再访问 Boss，就能确认"候选选择器在真实卡片结构上取得到值"。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from patchright.async_api import async_playwright

REPORT = Path(r"D:\VibeCoding\mianshi\scripts\boss_selectors_proposed.json")

#: 依据 link_ancestors 还原真实祖先链（a.job-name → div.job-title.clearfix → div.job-info
#: → li.job-card-box → div.job-card-wrap.active → div.card-area.is-seen → ul.rec-job-list）
WRAPPER = """
<ul class="rec-job-list">
  <div class="card-area is-seen">
    <div class="job-card-wrap active">{cards}</div>
  </div>
</ul>
"""

PROPOSED = {
    "job_card": "li.job-card-box",
    "card_title": "a.job-name",
    "card_link": "a.job-name",
    "card_company": "span.boss-name",
    "card_salary": "span.job-salary",
    "card_city": "span.company-location",
}


async def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    card_html = (report.get("listing_raw") or {}).get("first_card_html") or ""
    if not card_html:
        print("报告里没有 first_card_html，无法验证")
        return 1

    # 造两张卡片：第二张换掉标题/公司/地点，用来确认选择器是"按位置取"而不是取到固定文本
    second = (
        card_html.replace("视觉工程师", "视觉算法工程师")
        .replace("迁移科技", "某某视觉")
        .replace("北京·昌平区·北七家", "上海·浦东新区")
        .replace("61945b12a104c2d1031_2Ni0FFNU", "bbb222")
    )
    body = WRAPPER.format(cards=card_html + second)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        await page.set_content(body)

        total = await page.locator(PROPOSED["job_card"]).count()
        print(f"job_card='{PROPOSED['job_card']}' 命中 {total} 张（期望 2）")

        for i in range(total):
            card = page.locator(PROPOSED["job_card"]).nth(i)
            row = {"index": i}
            for name in ("card_title", "card_company", "card_salary", "card_city"):
                sel = PROPOSED[name]
                loc = card.locator(sel)
                row[name] = (await loc.first.inner_text()).strip() if await loc.count() else "<取不到>"
            link_loc = card.locator(PROPOSED["card_link"])
            row["card_link"] = await link_loc.first.get_attribute("href") if await link_loc.count() else "<取不到>"
            print("  ", json.dumps(row, ensure_ascii=False))

        print("\n--- 反例：我的旧启发式选到的 company-location（其实是地点） ---")
        loc = page.locator(PROPOSED["job_card"]).first.locator("span.company-location")
        print("   company-location 文本 =", (await loc.first.inner_text()).strip() if await loc.count() else "<取不到>")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
