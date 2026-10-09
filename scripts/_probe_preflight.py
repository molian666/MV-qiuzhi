"""飞行前检查：在**逼真的合成 DOM** 上跑一遍探针的全部逻辑。

这里刻意把真机上踩到的坑全摆进假页面，谁改坏了规则就直接红：
- 列表页：`span.company-location`（地点）与 `span.company-name` 同时存在，
  且 location 的 class 里带 "company"（偏好正则会给它加分）；薪资用 PUA 码点（文本正则匹配不到）；
- 卡片数 15 > 探针采样数 8（"命中率拿总数比"那个 bug 只在这个条件下暴露）；
- 详情页：页脚 `div.copyright`（≥120 字，会被当成 JD）、**两个**「立即沟通」按钮、
  `div.boss-info-attr`（HR 信息）、`span.boss-online-tag`（活跃标记，容易被当成 HR 名字）、
  经验/学历共用一个 class（需要 nth 消歧）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from patchright.async_api import async_playwright

BACKEND = Path(r"D:\VibeCoding\mianshi\backend")

MODULE = Path(r"D:\VibeCoding\mianshi\scripts\apply_boss_probe.py")
spec = importlib.util.spec_from_file_location("probe", MODULE)
probe = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(probe)  # type: ignore[union-attr]

#: Boss 的薪资是私用区码点（字体反爬），文本正则必然匹配不到，只能靠 class 语义发现
PUA_SALARY = "\ue032\ue033-\ue032\ue039K·\ue032\ue034薪"

JD_TEXT = (
    "【岗位职责】1.负责视觉系统的部署与调试，解答客户疑问并培训客户使用公司产品软件；"
    "2.及时准确反馈项目信息和现场情况，快速定位和解决产品使用问题，沉淀标准化文档；"
    "3.配合算法团队完成现场标定与精度验证。任职要求：熟悉 Halcon 或 VisionPro，"
    "能独立完成相机与镜头选型，有 3C 行业外观检测经验优先。"
)


def build_fake_list(count: int = 15) -> str:
    items = []
    for i in range(count):
        items.append(
            f"""
    <li class="job-card-wrapper">
      <a class="job-name" href="/job_detail/card{i}.html">视觉工程师{i}</a>
      <span class="company-name">某某视觉{i}</span>
      <span class="company-location">北京·昌平区{i}</span>
      <span class="job-salary">{PUA_SALARY}</span>
    </li>"""
        )
    return (
        """
<div class="page">
  <div class="search-bar"><input class="ipt-search" placeholder="搜索职位、公司" /><a class="btn-search">搜索</a></div>
  <ul class="job-list">"""
        + "".join(items)
        + """
  </ul>
</div>
"""
    )


FAKE_LIST = build_fake_list(15)

FAKE_DETAIL = f"""
<div class="job-detail">
  <div class="job-primary">
    <h1 class="name">视觉工程师</h1>
    <span class="salary">12-18K·13薪</span>
    <span class="text-desc text-experiece">3-5年</span>
    <span class="text-desc text-degree">大专</span>
  </div>
  <div class="job-detail-section">
    <div class="job-sec-text">{JD_TEXT}</div>
  </div>
  <div class="boss-info"><div class="boss-info-attr">迁移科技 · HR</div><span class="boss-online-tag">在线</span></div>
  <a class="btn btn-startchat">立即沟通</a>
  <div class="similar-jobs">
    <div class="similar-job"><a class="btn btn-startchat">立即沟通</a><span class="similar-job-name">图像算法工程师</span></div>
  </div>
  <div class="copyright">Copyright © 2026 BOSS直聘 京ICP备14013441号-5 京ICP证150923号 电子营业执照 京公网安备 11010502030159号 人力资源服务许可证 朝阳区人社局监督电话</div>
</div>
"""

FAKE_CHAT = """
<div class="chat-dialog">
  <div class="chat-editor"><textarea class="chat-input" placeholder="请输入消息"></textarea></div>
  <span class="btn-send">发送</span>
</div>
"""

#: 真机现场：一条岗位页面上 `div.job-sec-text` 命中 3 个，全是公司/薪资信息，**没有 JD**
MULTI_SEC_TEXT_NO_JD = """
<div class="job-detail-section">
  <div class="job-sec-text">公司介kanzhun绍：创想智直聘控成立于2013年，专注于机器视觉。</div>
  <div class="job-sec-text">薪资范围：10000-15000</div>
  <div class="job-sec-text fold-text">北京创想智控科技有限公司成立于2013年，是一家专注于机器视觉的研发及生产的高新技术企业。</div>
</div>
"""

#: 有 JD 的岗位：必须挑 JD，而不是公司介绍
MULTI_SEC_TEXT_WITH_JD = """
<div class="job-detail-section">
  <div class="job-sec-text">公司介绍：某某科技成立于2013年。</div>
  <div class="job-sec-text">【岗位职责】1.负责视觉系统的部署与调试；2.配合算法团队完成现场标定与精度验证。任职要求：熟悉 Halcon 或 VisionPro。</div>
</div>
"""

failures: list[str] = []


def check(label: str, actual: object, expected: object) -> None:
    ok = actual == expected
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f"  期望 {expected!r}"))
    if not ok:
        failures.append(label)


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()

        print("=== 列表页（15 张卡 > 采样 8 张；公司/地点同名干扰；PUA 薪资）===")
        await page.set_content(FAKE_LIST)
        raw = await page.evaluate(probe.PROBE_JS)
        cards = probe.verify_cards(raw)
        chosen = cards.get("verified") or {}
        for name, sel in sorted(chosen.items()):
            print(f"  {name:14s} = {sel}")
        check("job_card", chosen.get("job_card") or cards.get("chosen_container", {}).get("selector"), "li.job-card-wrapper")
        check("card_title", chosen.get("card_title"), "a.job-name")
        check("card_company", chosen.get("card_company"), "span.company-name")
        check("card_city", chosen.get("card_city"), "span.company-location")
        check("card_salary", chosen.get("card_salary"), "span.job-salary")
        check("card_link", chosen.get("card_link"), "a.job-name")
        check("per_card[0].card_company", (cards.get("per_card") or [{}])[0].get("card_company"), "某某视觉0")

        print("\n=== 详情页（页脚当 JD；两个沟通按钮；活跃标记当 HR 名字）===")
        await page.set_content(FAKE_DETAIL)
        detail = await page.evaluate(probe.DETAIL_JS)
        report = {"listing_raw": raw, "cards": cards, "detail": detail, "chat": {}}
        result = probe.build_proposal(report)
        proposed = result["proposed"]
        print(json.dumps(proposed, ensure_ascii=False, indent=2))
        check("detail_jd", proposed.get("detail_jd"), "div.job-sec-text")
        # 详情页薪资是正常文本（列表页才是字体反爬），校准它才能让薪资规则生效
        check("detail_salary", proposed.get("detail_salary"), "span.salary")
        check("greet_button", proposed.get("greet_button"), "a.btn.btn-startchat >> nth=0")
        check("hr_name", proposed.get("hr_name"), "div.boss-info-attr")
        check("hr_active", proposed.get("hr_active"), "span.boss-online-tag")
        # 真机上经验与学历是**两个不同的 class**，不需要 nth 消歧
        check("detail_experience", proposed.get("detail_experience"), "span.text-desc.text-experiece")
        check("detail_degree", proposed.get("detail_degree"), "span.text-desc.text-degree")

        print("\n=== 聊天框（窗口被关掉时不该崩）===")
        await page.set_content(FAKE_CHAT)
        chat = await page.evaluate(probe.CHAT_JS)
        check("greet_input", (chat.get("inputs") or [{}])[0].get("selector"), "textarea.chat-input")
        check("greet_send", (chat.get("sends") or [{}])[0].get("selector"), "span.btn-send")

        print("\n=== 详情页多命中：JD 按内容挑、薪资从『薪资范围：』兜底 ===")
        sys.path.insert(0, str(BACKEND))
        from app.apply.adapters.browser import BEST_TEXT_JS, SALARY_TEXT_JS  # noqa: PLC0415

        # 真机现场（这条岗位没有 JD，3 个 job-sec-text 全是公司/薪资信息）
        await page.set_content(MULTI_SEC_TEXT_NO_JD)
        picked = await page.evaluate(BEST_TEXT_JS, "div.job-sec-text")
        check("无 JD 时不把公司介绍当 JD", picked, "")
        check("薪资兜底（薪资范围：10000-15000）", await page.evaluate(SALARY_TEXT_JS), "10000-15000")

        # 有 JD 的岗位：必须挑到 JD，而不是公司介绍
        await page.set_content(MULTI_SEC_TEXT_WITH_JD)
        picked = await page.evaluate(BEST_TEXT_JS, "div.job-sec-text")
        check("有 JD 时挑到 JD", picked.startswith("【岗位职责】"), True)
        print(f"       挑到：{picked[:40]}…")

        await browser.close()

        # 关掉窗口时 _safe_eval 必须抛 ProbeAborted 而不是原始异常
        try:
            await probe._safe_eval(page, "1 + 1")
            failures.append("closed-page")
            print("  FAIL 关闭页面后 _safe_eval 没有抛 ProbeAborted")
        except probe.ProbeAborted:
            print("  OK   关闭页面后 _safe_eval 抛 ProbeAborted")

    print()
    if failures:
        print("预检失败：" + ", ".join(failures))
        return 1
    print("预检全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
