"""Boss 选择器自动校准探针（patchright + 真实 Chrome）。

它做的事：打开**应用同一个 profile**（`backend/data/browser/boss`）的 Boss 搜索页，
在页面上跑 DOM 探测，把「卡片容器 / 标题 / 公司 / 薪资 / 城市 / 链接 / 搜索框 /
风控与登录标记」的候选选择器连同**命中数量与实测文本**一起列出来，
再用和 `app/apply/adapters/browser.py` **完全相同的取法**逐张卡片试抽一遍作为验证。

设计原则和项目一致：不猜、不硬编码平台 DOM。脚本只**报告证据**，
要不要写进配置由你/我确认（`--apply` 模式才会写）。

它**只读页面**：不点击「立即沟通」、不发任何消息、不绕过验证码。
招呼语相关选择器需要你手动点开聊天框，脚本只在旁边等（可选阶段）。

用法：
    # 1. 采集证据（会弹出 Chrome，未登录就扫码；写完报告即退出）
    backend\\.venv\\Scripts\\python.exe scripts\\apply_boss_probe.py

    # 2. 确认报告后写进平台配置（不需要浏览器）
    backend\\.venv\\Scripts\\python.exe scripts\\apply_boss_probe.py --apply scripts\\boss_selectors_proposed.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

BACKEND_DIR = Path(r"D:\VibeCoding\mianshi\backend")
PROFILE_DIR = BACKEND_DIR / "data" / "browser" / "boss"
REPORT_PATH = Path(r"D:\VibeCoding\mianshi\scripts\boss_selectors_proposed.json")
API_BASE = "http://127.0.0.1:8000"
SEARCH_URL = "https://www.zhipin.com/web/geek/job?query={keyword}&city={city}"
ORIGIN = "https://www.zhipin.com"

#: 应用侧真正会 checks 的字段（分阶段），用来判断"能不能开始抓/投"
REQUIRED_FOR_SEARCH = ("search_input", "job_card", "card_title", "card_company")
REQUIRED_FOR_SEND = ("greet_button", "greet_input", "greet_send")

# --------------------------------------------------------------------------- #
# 页面内探测（只读 DOM，返回候选与证据，不做判断）
# --------------------------------------------------------------------------- #
PROBE_JS = r"""
() => {
  const q = (s, root) => { try { return Array.from((root || document).querySelectorAll(s)); } catch (e) { return []; } };
  const txt = (el) => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const classes = (el) => Array.from((el && el.classList) || [])
    .filter((c) => /^[a-zA-Z][A-Za-z0-9_-]*$/.test(c) && c.length <= 40)
    .filter((c) => !/^(css|jsx|sc|style)-/.test(c))
    .filter((c) => !/[0-9a-f]{8,}/.test(c));
  const css = (el) => {
    if (!el || !el.tagName) return '';
    const tag = el.tagName.toLowerCase();
    const cs = classes(el);
    if (cs.length) return tag + cs.slice(0, 2).map((c) => '.' + c).join('');
    const parts = [];
    let cur = el, depth = 0;
    while (cur && cur.tagName && depth < 3) {
      let n = 1, sib = cur.previousElementSibling;
      while (sib) { if (sib.tagName === cur.tagName) n++; sib = sib.previousElementSibling; }
      parts.unshift(cur.tagName.toLowerCase() + ':nth-of-type(' + n + ')');
      cur = cur.parentElement; depth++;
    }
    return parts.join(' > ');
  };
  const count = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return -1; } };
  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));

  const jobs = q('a[href*="job_detail"]');

  // 1) 卡片容器：从第一个岗位链接逐层向上，找"同类数量合理且每个都含岗位链接"的层
  const cardCandidates = [];
  if (jobs.length) {
    let el = jobs[0];
    for (let lvl = 0; lvl < 8 && el; lvl++) {
      const s = css(el);
      const n = count(s);
      const threshold = Math.max(3, Math.floor(jobs.length * 0.6));
      if (n >= threshold && n <= 150) {
        const withLink = q(s).filter((e) => e.querySelector('a[href*="job_detail"]')).length;
        if (withLink >= threshold) {
          cardCandidates.push({ selector: s, matches: n, with_link: withLink, level: lvl });
        }
      }
      el = el.parentElement;
    }
  }

  // 2) 卡片内字段候选
  const fields = { card_title: [], card_company: [], card_salary: [], card_city: [], card_link: [] };
  const cards = cardCandidates.length ? q(cardCandidates[cardCandidates.length - 1].selector) : [];
  if (cards.length) {
    const first = cards[0];
    const link = first.querySelector('a[href*="job_detail"]');
    if (link) {
      fields.card_link.push(css(link));
      if (txt(link)) fields.card_title.push(css(link));
      q('[class*="job-name"], [class*="jobName"], [class*="job-title"]', first).forEach((e) => fields.card_title.push(css(e)));
      q('span, div, p, h3', first).forEach((e) => {
        const t = txt(e);
        if (t && t.length <= 40 && !e.querySelector('span, div, p')) fields.card_title.push(css(e));
      });
    }
    q('[class*="company"], [class*="brand"], [class*="firm"]', first).forEach((e) => {
      const t = txt(e);
      if (t && t.length <= 60) fields.card_company.push(css(e));
    });
    q('span, div, p, em, i', first).forEach((e) => {
      const t = txt(e);
      if (!t || t.length > 30) return;
      if (/^\d+\s*[-–~至]\s*\d+\s*(K|k|千|万)/.test(t) || /^\d+\s*(K|k|千|万)\s*[-–~至]/.test(t)) fields.card_salary.push(css(e));
      if (/^(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆|天津|郑州|合肥|福州|厦门|济南|青岛|宁波|无锡|东莞|佛山|珠海|大连|沈阳|哈尔滨|昆明|贵阳|南昌|南宁|太原|石家庄|兰州|乌鲁木齐|呼和浩特|银川|西宁|拉萨|海口|三亚)(市)?/.test(t) && t.length <= 24) fields.card_city.push(css(e));
    });
    // 薪资数字被平台用私用区码点替换（Boss 就是这么干的），上面的"按文本找薪资"必然匹配不到，
    // 所以再按 class 语义补一轮——否则 card_salary 永远提不出来。
    q('[class*="salary"], [class*="Salary"]', first).forEach((e) => { if (txt(e)) fields.card_salary.push(css(e)); });
    q('[class*="job-area"], [class*="city"], [class*="location"], [class*="area"]', first).forEach((e) => { if (txt(e)) fields.card_city.push(css(e)); });
  }

  // 3) 搜索框
  const searchInputs = [];
  q('input').forEach((e) => {
    const ph = (e.getAttribute('placeholder') || '') + (e.getAttribute('name') || '') + (e.getAttribute('class') || '');
    if (/搜索|职位|公司|query|search/i.test(ph) || e.type === 'search') {
      searchInputs.push({ selector: css(e), matches: count(css(e)), placeholder: e.getAttribute('placeholder') || '', visible: !!(e.offsetWidth || e.offsetHeight) });
    }
  });

  // 4) 风控 / 登录标记
  const captchaPatterns = ['#captcha', '.geetest_panel', '.geetest_holder', '.geetest_box', 'iframe[src*="captcha"]',
    '[class*="captcha"]', '[class*="verify"]', '.dialog-wrap[class*="verify"]', '[id*="captcha"]'];
  const loginPatterns = ['a[href*="login"]', '[class*="sign-in"]', '[class*="login-btn"]', '[class*="login"]',
    '.header-login', '[class*="user-nav"]', '[class*="avatar"]', '[class*="user-info"]'];

  // 把"第一张卡片的 HTML + 岗位链接的 class 链"一起带回报告：
  // 若启发式规则在真机上没命中，凭这份证据就能离线改规则，不用再让你登录一次
  let first_card_html = '';
  const link_ancestors = [];
  if (cardCandidates.length && jobs.length) {
    const deepest = cardCandidates[cardCandidates.length - 1];
    const el = q(deepest.selector)[0];
    if (el) first_card_html = el.outerHTML.slice(0, 3000);
    let cur = jobs[0];
    while (cur && cur.tagName && link_ancestors.length < 7) {
      const cn = (cur.className || '').toString().trim().split(/\s+/).filter(Boolean).join('.');
      link_ancestors.push(cur.tagName.toLowerCase() + (cn ? '.' + cn : ''));
      cur = cur.parentElement;
    }
  }

  // ---- 卡片字段证据：**全部在同一次求值里算完** ----
  // 为什么必须同一次求值：实测这个 SPA（Boss 结果页）会在几秒内把整个列表换掉
  // （风控跳转链 `_security_check=1→5`），"JS 采候选 + Playwright 事后复核"的写法
  // 三次都稳定地复核到 0 个元素。同一个 DOM 快照里算，就不存在这个竞态。
  const fieldNames = Object.keys(fields);
  const cardEvidence = {};
  cardCandidates.forEach((cand) => {
    let all = [];
    try { all = q(cand.selector); } catch (e) { all = []; }
    const sample = all.slice(0, 8);
    const hits = {};
    fieldNames.forEach((name) => {
      hits[name] = fields[name].map((sel) => {
        let n = 0;
        const picked = [];
        sample.forEach((card) => {
          let el = null;
          try { el = card.querySelector(sel); } catch (e) { el = null; }
          if (!el) return;
          n += 1;
          if (picked.length < 3) {
            picked.push(name === 'card_link' ? (el.getAttribute('href') || '') : (el.innerText || '').trim());
          }
        });
        return { selector: sel, hit_cards: n, of_cards: sample.length, samples: picked };
      });
    });
    // 每张卡 × 每个字段 × 每个候选选择器各自取到什么值：
    // Python 选完选择器后可以直接从这里拼出"逐卡实抽"表，不必再碰页面
    const perCard = sample.map((card) => {
      const row = {};
      fieldNames.forEach((name) => {
        const cell = {};
        fields[name].forEach((sel) => {
          let el = null;
          try { el = card.querySelector(sel); } catch (e) { el = null; }
          if (el) cell[sel] = name === 'card_link' ? (el.getAttribute('href') || '') : (el.innerText || '').trim();
        });
        row[name] = cell;
      });
      return row;
    });
    cardEvidence[cand.selector] = { count: all.length, hits: hits, per_card: perCard };
  });

  return {
    url: location.href,
    page_title: document.title,
    jobs_count: jobs.length,
    // 骨架卡片也有 job_detail 链接之外的占位结构，所以"有链接"不等于"加载完了"：
    // 还要看链接里有没有标题文本
    titled_links: jobs.filter((a) => (a.innerText || '').trim().length > 0).length,
    security_check: /_security_check=|安全验证|请稍候|verify/i.test(location.href + ' ' + document.title),
    // 登录态判定要**找死证据**，而且证据本身要能复核（写进报告）：
    // Boss 的未登录页也有一个空的 [class*="avatar"] 占位元素，光看头像 img 会误判成已登录；
    // 真正可靠的判据是**头部导航里还有没有「登录/注册」入口**（登录后就消失）。
    header_text: Array.from(
      document.querySelectorAll('header, [class*="header"], [class*="user-nav"], [class*="nav-wrap"]')
    ).map((e) => e.innerText || '').join(' ').replace(/\s+/g, ' ').trim().slice(0, 200),
    avatar_html: (() => {
      const a = document.querySelector('[class*="avatar"], [class*="user-nav"] img, [class*="nav-figure"]');
      return a ? a.outerHTML.slice(0, 200) : '';
    })(),
    on_job_list: /\/web\/geek\/jobs?\b/.test(location.pathname),
    logged_in: (() => {
      const headerText = Array.from(
        document.querySelectorAll('header, [class*="header"], [class*="user-nav"], [class*="nav-wrap"]')
      ).map((e) => e.innerText || '').join(' ').replace(/\s+/g, ' ');
      const loginEntry = /登录\s*\/\s*注册|(^|\s)登录(\s|$)/.test(headerText)
        || Array.from(document.querySelectorAll('a, span, div'))
          .filter((e) => e.children.length === 0)
          .some((e) => /^登录(\/注册)?$/.test((e.innerText || '').trim()));
      const avatar = document.querySelector(
        '[class*="avatar"] img, [class*="user-nav"] img, [class*="nav-figure"] img, [class*="user-info"] img'
      );
      return !!avatar && !loginEntry;
    })(),
    has_login_text: /登录|注册/.test(document.body.innerText.slice(0, 4000)),
    card_candidates: cardCandidates,
    card_evidence: cardEvidence,
    fields: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, uniq(v)])),
    search_inputs: searchInputs,
    captcha_candidates: captchaPatterns.map((s) => ({ selector: s, matches: count(s) })),
    login_candidates: loginPatterns.map((s) => ({ selector: s, matches: count(s), sample: txt(document.querySelector(s)).slice(0, 40) })),
    body_head: document.body.innerText.replace(/\s+/g, ' ').slice(0, 300),
    first_card_html: first_card_html,
    link_ancestors: link_ancestors,
  };
}
"""

DETAIL_JS = r"""
() => {
  const q = (s) => { try { return Array.from(document.querySelectorAll(s)); } catch (e) { return []; } };
  const txt = (el) => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const classes = (el) => Array.from((el && el.classList) || [])
    .filter((c) => /^[a-zA-Z][A-Za-z0-9_-]*$/.test(c) && c.length <= 40)
    .filter((c) => !/^(css|jsx|sc|style)-/.test(c));
  const css = (el) => {
    if (!el || !el.tagName) return '';
    const cs = classes(el);
    if (cs.length) return el.tagName.toLowerCase() + cs.slice(0, 2).map((c) => '.' + c).join('');
    return el.tagName.toLowerCase();
  };
  const count = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return -1; } };
  // 同一个 class 常常同时承载"经验"和"学历"两个字段，单选择器会永远取到第一个命中，
  // 所以额外给出 "选择器 >> nth=N" 这种 Playwright 精确写法供消歧。
  const nth = (e) => {
    const s = css(e);
    try {
      const arr = q(s);
      const i = arr.indexOf(e);
      return i >= 0 ? s + ' >> nth=' + i : s;
    } catch (err) { return s; }
  };
  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));

  const out = { url: location.href, page_title: document.title, jd: [], meta: [], hr: [], salary: [], greet: [], body_head: '' };

  // 详情页薪资候选：列表页的薪资数字被字体反爬抹掉了，详情页是正常文本，
  // 所以这个字段值不值得校准直接决定"薪资区间"规则能不能生效
  q('[class*="salary"], [class*="Salary"]').forEach((e) => {
    const t = txt(e);
    if (t && t.length <= 30 && !e.querySelector('span, div, p')) out.salary.push({ selector: css(e), text: t });
  });
  out.salary = out.salary.slice(0, 6);

  // JD 正文候选：**优先含 JD 关键词的块**，并排掉页脚/登录框/安全提示这类噪音。
  // 上一版按"长度升序取最小的几个"结果选到了 div.copyright（页脚版权），完全跑偏。
  q('div, section, article').forEach((e) => {
    const t = txt(e);
    if (t.length < 120 || t.length > 8000) return;
    const cls = (e.className || '').toString();
    if (/copyright|footer|icp|sign-|login|dropdown|security-box|tag-container|app-icon|similar/i.test(cls)) return;
    let depth = 0, cur = e;
    while (cur && cur.parentElement) { depth += 1; cur = cur.parentElement; }
    out.jd.push({
      selector: css(e),
      length: t.length,
      depth: depth,
      jd_like: /岗位职责|任职要求|职位描述|岗位要求|工作职责|职位要求|工作内容/.test(t),
      head: t.slice(0, 60),
    });
  });
  // 含 JD 关键词的排前面；同样长时**取更深的那个**（父容器与子容器的 innerText 往往一字不差，
  // 谁在前纯看文档顺序，取值会莫名其妙地落到父容器上）
  out.jd = out.jd
    .sort((a, b) => (Number(b.jd_like) - Number(a.jd_like)) || (a.length - b.length) || (b.depth - a.depth))
    .slice(0, 8);

  // 经验/学历候选：只取叶子节点，否则会把整个"岗位信息"容器当经验要求
  q('span, div, p, li, em, i').forEach((e) => {
    const t = txt(e);
    if (!t || t.length > 20) return;
    if (e.querySelector('span, div, p, li, em, i')) return;
    if (/(经验|学历|本科|大专|硕士|博士|应届|不限|中专|高中|初中|在校)/.test(t)
        || /^\d+\s*[-~至]?\s*\d*\s*年/.test(t)) out.meta.push({ selector: css(e), nth_selector: nth(e), text: t });
  });
  out.meta = out.meta.slice(0, 20);

  // HR 名字/活跃标记候选：同样只取叶子节点。
  // 注意 Boss 的岗位标题是 <h1 class="name">，也含 "name"，必须靠 class 语义与文本排除掉。
  q('[class*="boss"], [class*="hr"], [class*="recruit"], [class*="name"], [class*="online"], [class*="active"], [class*="job-status"]').forEach((e) => {
    const t = txt(e);
    if (!t || t.length > 30) return;
    if (e.querySelector('span, div, p, h1, h2')) return;
    out.hr.push({ selector: css(e), nth_selector: nth(e), text: t, cls: (e.className || '').toString().slice(0, 60), tag: e.tagName.toLowerCase() });
  });
  out.hr = out.hr.slice(0, 25);

  // 「立即沟通」按钮（只定位，不点击）：叶子节点，避免选到装着它的整块容器
  q('a, button, span, div').forEach((e) => {
    const t = txt(e);
    if (t && t.length <= 12 && /(立即沟通|继续沟通|打招呼|沟通)/.test(t) && !e.querySelector('a, button, span, div')) {
      out.greet.push({ selector: css(e), text: t, tag: e.tagName.toLowerCase(), matches: count(css(e)) });
    }
  });
  out.greet = out.greet.slice(0, 15);

  out.body_head = document.body.innerText.replace(/\s+/g, ' ').slice(0, 300);
  return out;
}
"""

CHAT_JS = r"""
() => {
  const q = (s) => { try { return Array.from(document.querySelectorAll(s)); } catch (e) { return []; } };
  const txt = (el) => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const classes = (el) => Array.from((el && el.classList) || []).filter((c) => /^[a-zA-Z][A-Za-z0-9_-]*$/.test(c) && c.length <= 40);
  const css = (el) => {
    if (!el || !el.tagName) return '';
    const cs = classes(el);
    return cs.length ? el.tagName.toLowerCase() + cs.slice(0, 2).map((c) => '.' + c).join('') : el.tagName.toLowerCase();
  };
  const out = { inputs: [], sends: [] };
  // 只认"聊天输入框"：普通 input[type=text] 会把搜索框也算进来
  // （上一版就是这样把 greet_input 校准成了列表页的搜索框）
  q('textarea, [contenteditable="true"], input[type="text"]').forEach((e) => {
    const vis = !!(e.offsetWidth || e.offsetHeight);
    if (!vis) return;
    const tag = e.tagName.toLowerCase();
    const ph = e.getAttribute('placeholder') || '';
    const isPlainInput = tag === 'input';
    if (isPlainInput && !/消息|沟通|打招呼|请输入|回复/.test(ph)) return;
    out.inputs.push({ selector: css(e), tag: tag, placeholder: ph, visible: vis });
  });
  q('a, button, span, div').forEach((e) => {
    const t = txt(e);
    // 叶子节点才算发送按钮：容器的 innerText 也可能是"发送"，点到容器是点不动按钮的
    if (t && t.length <= 8 && /^(发送|发送消息|发 送)$/.test(t) && !e.querySelector('a, button, span, div')) {
      out.sends.push({ selector: css(e), text: t, tag: e.tagName.toLowerCase() });
    }
  });
  return out;
}
"""


# --------------------------------------------------------------------------- #
# 证据整理：JS 给候选，Python 用 Playwright 独立复核
# --------------------------------------------------------------------------- #
class ProbeAborted(Exception):
    """探针被外部打断（窗口被关掉等），不是代码 bug。"""


async def _safe_eval(page, js: str) -> dict | None:
    """求值页面 JS；**页面正在跳转或窗口被关掉时不要崩**。

    上一版就是在这里崩的：你把 Chrome 窗口关掉的那一刻，脚本正好在做"静置复核"，
    TargetClosedError 直接冒到顶层，整轮采集以 traceback 结束。
    """
    if page.is_closed():
        raise ProbeAborted("Chrome 窗口被关闭了")
    try:
        return await page.evaluate(js)
    except Exception as exc:  # noqa: BLE001
        if page.is_closed():
            raise ProbeAborted("Chrome 窗口被关闭了") from exc
        print(f"[probe] ...页面正在跳转（{type(exc).__name__}），继续等", flush=True)
        return None


async def gather(
    page, keyword: str, wait_seconds: int, city: str = "", allow_logged_out: bool = False
) -> dict:
    """打开搜索页，等到"**真的**加载完并且已登录"为止。

    这里踩过坑，所以门槛卡得死：
    - `jobs_count` 单独不够：骨架卡片/中间态也会让数字变正，必须要求 `titled_links > 0`；
    - 必须 `logged_in`：未登录页的卡片结构与登录后不同，拿它校准等于校准了另一个页面；
    - URL 带 `_security_check=`（Boss 的风控跳转）时立刻放弃，绝不拿被风控的页面当基准。
    """
    url = SEARCH_URL.format(keyword=keyword, city=city)
    print(f"[probe] 打开 {url}", flush=True)
    await page.goto(url, wait_until="domcontentloaded")

    print(f"[probe] 等待**已登录的搜索结果**（最多 {wait_seconds}s）。"
          "若浏览器里显示未登录，请扫码登录（登录后脚本会自己继续）。", flush=True)
    deadline = asyncio.get_event_loop().time() + wait_seconds
    raw: dict = {}
    check_since: float | None = None
    warned_check = False
    re_navigations = 0
    last_nav = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() < deadline:
        fresh = await _safe_eval(page, PROBE_JS)
        if fresh is None:
            await asyncio.sleep(5)
            continue
        raw = fresh
        now = asyncio.get_event_loop().time()

        # 判据只看**页面内容**：URL 里的 _security_check 会出现在跳转链上，
        # 挑战走完页面照样能正常渲染（上一版就是凭 URL 过早放弃，错过了真实结果）
        real_results = raw.get("titled_links", 0) >= 3 and bool(raw.get("on_job_list"))
        logged_in = bool(raw.get("logged_in")) and real_results

        if logged_in or (allow_logged_out and real_results):
            # 让页面再静置一会儿再做二次采样。
            # **必须比 DOM，而不是比 URL**：实测 URL 带 `_security_check=1` 时，SPA 在采样后
            # 会把列表整块换掉，URL 却一字不变——上一版就是只比 URL，结果 Playwright 复核时
            # 容器已经变成 0 个，所有字段校验全空。
            await asyncio.sleep(3)
            confirm = await _safe_eval(page, PROBE_JS)
            same_dom = bool(confirm) and confirm.get("first_card_html") == raw.get("first_card_html")
            if (confirm or {}).get("titled_links", 0) >= 3 and same_dom:
                confirm = dict(confirm or {})
                confirm["status"] = "ok_logged_in" if logged_in else "ok_logged_out"
                confirm["dom_stable"] = True
                return confirm
            if confirm is not None:
                print("[probe] ...页面在采样后变化了（SPA 重渲染），重新采样", flush=True)
                raw = confirm
            continue

        # 登录完成后页面常常停在首页/个人页，这时要自己回到搜索页（限次数，别把风控惹毛）
        if raw.get("logged_in") and not raw.get("on_job_list") and re_navigations < 3 and now - last_nav > 45:
            re_navigations += 1
            last_nav = now
            print(f"[probe] 已登录但不在搜索页（{raw.get('page_title')!r}）→ 回到搜索页"
                  f"（第 {re_navigations}/3 次）", flush=True)
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            continue

        if raw.get("security_check") and not real_results:
            check_since = check_since or now
            if not warned_check:
                warned_check = True
                print("[probe] 检测到 Boss 的『请稍候』安全验证页 → "
                      "**请在弹出的窗口里手动把验证过掉**（有滑块就拖一下），脚本会一直等。", flush=True)
            if now - check_since > 300:
                raw["status"] = "security_check"
                return raw
        else:
            check_since = None

        await asyncio.sleep(5)
        print(
            f"[probe] ...登录={'是' if raw.get('logged_in') else '否'}"
            f"{'（但页面还没有结果）' if raw.get('logged_in') and not real_results else ''} "
            f"岗位链接={raw.get('jobs_count')} 带标题={raw.get('titled_links')} "
            f"标题={raw.get('page_title','')!r}"
            f"{' [风控挑战中]' if raw.get('security_check') and not real_results else ''}",
            flush=True,
        )

    raw["status"] = "timeout"
    return raw


def verify_cards(raw: dict) -> dict:
    """从**同一次 JS 求值**带回来的证据里挑选择器（不再事后碰页面）。

    为什么不事后用 Playwright 复核：Boss 的结果页是个会在几秒内整块换掉的 SPA
    （风控跳转链），实测"JS 采候选 + Playwright 复核"三次都复核到 0 个元素——
    复核本身成了竞态。`card_evidence` 是 JS 在同一个 DOM 快照里算出来的命中数与样本，
    用它选选择器既准确又没有竞态。

    容器不能只认"最深一层"：`div.job-title` / `div.job-info` / `li.job-card-box` 每层都恰好
    对应一个岗位，深的那个不一定装着公司名与薪资。所以逐层试，取第一个能验证出
    title+company 的层（这才是真正一张卡片的范围）。
    """
    report: dict = {"candidates": {}, "verified": {}, "per_card": []}
    cards = raw.get("card_candidates") or []
    evidence = raw.get("card_evidence") or {}
    if not cards:
        return report

    report["candidates"]["job_card"] = cards
    #: 每个字段"看起来像什么"的语义偏好：同样的命中率下优先选名字对得上的
    prefer = {
        "card_title": r"^a|job-name|jobName|job-title|title",
        "card_company": r"company|brand|firm|shop",
        "card_salary": r"salary|pay|wage",
        "card_city": r"area|city|location|address",
        "card_link": r"^a",
    }
    #: 反向偏好：名字里带这些词的元素**看起来像**某字段、其实是另一个字段。
    #: `span.company-location` 就是典型：命中 "company" 加了分，结果把地点当成了公司名。
    penalize = {
        "card_company": r"location|area|address|city|region",
    }
    # 先选城市，再用城市样本排掉公司的误命中（见下面的 samples 互斥检查）
    field_order = ["card_city", "card_company", "card_link", "card_title", "card_salary"]

    best: dict | None = None
    for container in sorted(cards, key=lambda c: -c["level"]):
        ev = evidence.get(container["selector"]) or {}
        count = int(ev.get("count") or 0)
        hits = ev.get("hits") or {}
        chosen: dict[str, str] = {}
        chosen_samples: dict[str, list[str]] = {}
        rows_by_field: dict[str, list] = {}
        if count:
            for name in field_order:
                rows = list(hits.get(name) or [])

                def rank(row: dict, _name: str = name) -> tuple[int, int]:
                    bonus = 2 if re.search(prefer.get(_name, ""), row["selector"]) else 0
                    if re.search(penalize.get(_name, r"(?!)"), row["selector"]):
                        bonus -= 3
                    return (int(row["hit_cards"]) + bonus, -len(row["selector"]))

                rows.sort(key=rank, reverse=True)
                rows_by_field[name] = rows
                # ⚠️ 命中率必须跟**样本数**比，不能跟容器总数比：
                # `hit_cards` 只在前 8 张样本卡里统计，拿它跟"页面共 15 张"比是永远不可能成立的
                # （8 < 14），会让每个字段都"验证失败"。这个坑踩过：三次真机采集全空都是它。
                good = next(
                    (
                        r
                        for r in rows
                        if int(r["hit_cards"]) >= max(1, int(r["of_cards"]) - 1)
                        and any(str(s).strip() for s in r["samples"])
                        # 标题不能就是公司名（否则会拿去当岗位名，招呼语会写成"某某公司"在招人）
                        and not (name == "card_title" and chosen_samples.get("card_company")
                                 and r["samples"] == chosen_samples["card_company"])
                        # 公司不能就是城市（Boss 卡片的 span.company-location 是地点，
                        # 它的 class 里带 "company"，很容易被当成公司名——实测就是这么错的）
                        and not (name == "card_company" and chosen_samples.get("card_city")
                                 and r["samples"] == chosen_samples["card_city"])
                    ),
                    None,
                )
                if good:
                    chosen[name] = good["selector"]
                    chosen_samples[name] = good["samples"]

        # 逐卡实抽：直接用 JS 带回来的"每卡 × 每选择器"取值表
        per_card = []
        for index, row in enumerate(ev.get("per_card") or []):
            item: dict = {"index": index}
            for name, sel in chosen.items():
                item[name] = (row.get(name) or {}).get(sel, "")
            if chosen.get("card_link"):
                item["link"] = item.pop("card_link", "")
            per_card.append(item)

        attempt = {"container": container, "chosen": chosen, "rows_by_field": rows_by_field,
                   "per_card": per_card, "count": count}
        report.setdefault("container_attempts", []).append(
            {"container": container, "verified": sorted(chosen.keys()), "card_count": count}
        )
        if "card_title" in chosen and "card_company" in chosen:
            best = attempt
            break
        if best is None and chosen:
            best = attempt

    if best is None:
        report["dom_changed"] = (
            "同一次 JS 求值里也没有任何容器候选能取到 岗位名+公司名（容器数："
            + ", ".join(f"{c['selector']}={int((evidence.get(c['selector']) or {}).get('count') or 0)}" for c in cards)
            + "）——本轮结果不可信，不要写进配置。"
        )
        print("[probe] ⚠ " + report["dom_changed"], flush=True)
        return report

    report["chosen_container"] = best["container"]
    report["candidates"].update(best["rows_by_field"])
    report["verified"] = best["chosen"]
    report["per_card"] = best["per_card"]
    return report


async def verify_detail(page, card_link: str) -> dict:
    url = card_link if card_link.startswith("http") else ORIGIN + card_link
    print(f"[probe] 抓详情页证据：{url}", flush=True)
    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.sleep(3)
    data = await _safe_eval(page, DETAIL_JS)
    return data or {}


async def wait_for_chat(context, seconds: int) -> dict:
    """等你手动点开聊天框。

    **必须扫所有标签页**：Boss 的「立即沟通」可能是新开标签页，
    只看当前页会永远等不到（顺带也验证了适配器"单页流程"是否成立）。
    """
    print("[probe] 如果你想同时校准招呼语选择器：请在 Chrome 里点任意岗位的「立即沟通」打开聊天框，"
          f"脚本会等 {seconds}s 自动检测（新标签页也会看；不点也行）", flush=True)
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        for candidate in context.pages:
            # 列表页永远有搜索框，跳过它，免得把搜索框当聊天输入框
            if "/web/geek/job" in candidate.url:
                continue
            if candidate.is_closed():
                continue
            try:
                data = await candidate.evaluate(CHAT_JS)
            except Exception:  # noqa: BLE001 - 页面正在跳转/单个标签被关掉，跳过它就好
                continue
            if not data:
                continue
            if data.get("inputs"):
                data["found_on"] = candidate.url
                data["page_count"] = len(context.pages)
                return data
        await asyncio.sleep(5)
    return {"inputs": [], "sends": [], "timeout": True, "page_count": len(context.pages)}


# --------------------------------------------------------------------------- #
async def run_check(args) -> int:
    """只报告这个 profile 现在的登录态与页面状态（跑探针前先问一句）。

    ⚠️ **默认有头**：Boss 对 headless 的识别率明显更高，用 headless 去查等于白送一次
    风控信号（实测 headless 那次直接陷进 `请稍候` 的 `_security_check` 死循环）。
    也正因为它本身算一次自动访问，**不要反复跑**。
    """
    from patchright.async_api import async_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=False, no_viewport=True
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(
                SEARCH_URL.format(keyword=args.keyword, city=args.city), wait_until="domcontentloaded"
            )
            await asyncio.sleep(6)
            data = await _safe_eval(page, PROBE_JS) or {}
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass

    print(json.dumps(
        {
            "logged_in": data.get("logged_in"),
            "on_job_list": data.get("on_job_list"),
            "jobs_count": data.get("jobs_count"),
            "titled_links": data.get("titled_links"),
            "security_check": data.get("security_check"),
            "page_title": data.get("page_title"),
            "url": data.get("url"),
        },
        ensure_ascii=False,
        indent=2,
    ))
    if data.get("logged_in"):
        print("→ 已登录：可以直接跑探针，能一次拿全（含详情页与招呼语）")
    elif data.get("titled_links", 0) >= 3:
        print("→ 未登录，但**有搜索结果**：加 --allow-logged-out 可先校准『抓候选』需要的列表页选择器")
    else:
        print("→ 未登录且没有结果：需要先在有头窗口里登录")
    return 0


async def run_probe(args) -> int:
    from patchright.async_api import async_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {"keyword": args.keyword, "profile": str(PROFILE_DIR)}

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        aborted = ""
        try:
            listing = await gather(
                page, args.keyword, args.wait, args.city,
                allow_logged_out=args.allow_logged_out,
            )
            report["listing_raw"] = listing
            status = listing.get("status")

            if status == "security_check":
                report["error"] = (
                    "Boss 把这次访问拖在风控挑战页上超过 90 秒："
                    f"{listing.get('url')}。请先手动在浏览器里把验证走完再重跑。"
                )
                print("[probe] 放弃：" + report["error"], flush=True)
            elif status not in {"ok_logged_in", "ok_logged_out"}:
                report["error"] = (
                    f"没有等到可信的搜索结果（status={status}，"
                    f"logged_in={listing.get('logged_in')}，带标题链接={listing.get('titled_links')}，"
                    f"是否列表页={listing.get('on_job_list')}）。"
                    "页面标题：" + str(listing.get("page_title"))
                )
                print("[probe] 放弃：" + report["error"], flush=True)
            else:
                if status == "ok_logged_out":
                    report["warning"] = (
                        "这是**未登录**页面采到的结果。抓候选（列表页）大概率能用，"
                        "但详情页/打招呼必须登录后重新校准——不要把它当成校准完成。"
                    )
                    print("[probe] ⚠ " + report["warning"], flush=True)
                report["cards"] = verify_cards(listing)

                # 详情页与聊天框只在**已登录**时才采：未登录时点开的是登录墙，采了也是错的
                if status == "ok_logged_out":
                    report["skipped_stages"] = ["detail", "chat"] 
                    print("[probe] 未登录 → 跳过详情页与聊天框阶段（那里是登录墙，采了没用）", flush=True)
                else:
                    # 详情页（只导航，不点击）
                    link = ""
                    for row in report["cards"].get("per_card", []):
                        if row.get("link"):
                            link = row["link"]
                            break
                    if link and not args.skip_detail:
                        report["detail"] = await verify_detail(page, link)

                    if not args.skip_greet:
                        report["chat"] = await wait_for_chat(context, args.greet_wait)
        except ProbeAborted as exc:
            aborted = str(exc)
            report["error"] = f"{exc}（采集被打断，本轮没有可用结果）"
            print("[probe] 中断：" + report["error"], flush=True)
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001 - 窗口已经被关掉了
                pass

    if aborted:
        report["aborted"] = aborted

    report.update(build_proposal(report))
    # 采集失败时**绝不输出可写入的建议**：风控页/未登录页上也能凑出 search_input、login_marker，
    # 一旦被人 `--apply` 写进配置，就是拿错的选择器去投真实岗位
    if (report.get("listing_raw") or {}).get("status") not in {"ok_logged_in", "ok_logged_out"}:
        blocked = dict(report.get("proposed") or {})
        report["proposed"] = {}
        report["rejected_proposal"] = blocked
        report["missing_for_search"] = list(REQUIRED_FOR_SEARCH)
        report["missing_for_send"] = list(REQUIRED_FOR_SEND)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(report)
    return 0


def build_proposal(report: dict) -> dict:
    """把页面证据汇总成"建议写入的选择器"（独立成函数，便于在合成 DOM 上离线验证）。"""
    collisions: list[str] = []
    proposed: dict[str, str] = {}
    cards = report.get("cards") or {}
    # 卡片容器单独放在 chosen_container（它不是"字段行"，而是外层容器）
    container = cards.get("chosen_container") or {}
    if container.get("selector"):
        proposed["job_card"] = container["selector"]
    for key, sel in (cards.get("verified") or {}).items():
        if key != "card_link":
            proposed[key] = sel
    if cards.get("verified", {}).get("card_link"):
        proposed["card_link"] = cards["verified"]["card_link"]

    for item in (report.get("listing_raw", {}).get("search_inputs") or []):
        if item.get("visible") and item.get("matches") == 1:
            proposed["search_input"] = item["selector"]
            break

    for item in (report.get("listing_raw", {}).get("captcha_candidates") or []):
        if item.get("matches", 0) > 0:
            proposed["captcha_marker"] = item["selector"]
            break

    login_raw = report.get("listing_raw", {})
    if login_raw.get("has_login_text"):
        for item in (login_raw.get("login_candidates") or []):
            if item.get("matches", 0) == 1 and re.search(r"登录", item.get("sample") or ""):
                proposed["login_marker"] = item["selector"]
                break

    detail = report.get("detail") or {}
    jd_rows = detail.get("jd") or []
    # 优先含"岗位职责/任职要求"这类关键词的块（上一版按长度升序选到了页脚版权）
    best_jd = next((row for row in jd_rows if row.get("jd_like")), jd_rows[0] if jd_rows else None)
    if best_jd:
        proposed["detail_jd"] = best_jd["selector"]

    # 详情页薪资：列表页那份被字体反爬（数字读不出来），详情页是正常文本 → 值得校准
    salary_rows = [s for s in (detail.get("salary") or []) if s.get("text")]
    if salary_rows:
        # 优先能看出"数字 + K"形状的那个（那才是薪资，而不是"薪资面议"之类的标签）
        salary_rows.sort(key=lambda s: (not re.search(r"\d", s["text"]), len(s["text"])))
        proposed["detail_salary"] = salary_rows[0]["selector"]

    # 「立即沟通」：优先 tag 是按钮/链接、且**只命中一个**的候选。
    # browser.py 是直接 page.click(选择器)，命中多个时点到哪个不确定（实测页面上有 2 个
    # `a.btn.btn-startchat`），所以多命中时显式加 `>> nth=0`（页面上第一个才是主按钮）。
    greet_buttons = [g for g in (detail.get("greet") or []) if g.get("text") in {"立即沟通", "继续沟通", "打招呼"}]
    greet_buttons.sort(key=lambda g: (g.get("tag") not in {"a", "button"}, len(g["selector"])))
    if greet_buttons:
        chosen_greet = greet_buttons[0]
        selector = chosen_greet["selector"]
        proposed["greet_button"] = (
            f"{selector} >> nth=0" if int(chosen_greet.get("matches") or 1) > 1 else selector
        )

    # 经验/学历：按内容判定，而不是只找"经验"两个字（Boss 常写成 "3-5年"）
    meta = [m for m in (detail.get("meta") or []) if m.get("text")]
    exp = next((m for m in meta if re.search(r"^(\d+\s*[-~至]?\s*\d*\s*年|经验|应届|在校|不限)", m["text"])), None)
    degree = next((m for m in meta if re.search(r"本科|大专|硕士|博士|中专|高中|初中|学历", m["text"])), None)
    # 两者共用同一个 class 时必须用 nth 消歧：browser.py 的 inner_text() 只取第一个命中，
    # 光靠 class 的话学历会被读成"3-5年"（学历过滤规则就会失效）
    if exp and degree and exp["selector"] == degree["selector"]:
        proposed["detail_experience"] = exp.get("nth_selector") or exp["selector"]
        proposed["detail_degree"] = degree.get("nth_selector") or degree["selector"]
        collisions.append(
            f"经验与学历共用选择器 {exp['selector']}，已改用 {exp.get('nth_selector')} / {degree.get('nth_selector')}"
        )
    else:
        if exp:
            proposed["detail_experience"] = exp["selector"]
        if degree:
            proposed["detail_degree"] = degree["selector"]

    # HR 名字：优先 class 语义（boss-info-attr / boss-name / hr-name），并排除两类误命中：
    # 1) 岗位标题（Boss 也是 <h1 class="name"> / <div class="name">，相似岗位列表里一堆）；
    # 2) **活跃标记**（`span.boss-online-tag` 的文本是"在线/刚刚活跃"，实测被当成 HR 名字了）。
    hr = [
        h for h in (detail.get("hr") or [])
        if h.get("text") and not re.search(r"在线|活跃|刚刚|本周|今日|回复", h["text"])
    ]
    hr.sort(key=lambda h: (
        0 if re.search(r"boss-info-attr|boss-name|hr-name|recruiter-name|boss_name", h.get("cls") or "") else 1,
        1 if h.get("tag") == "h1" else 0,
        1 if re.search(r"job|company|salary|title|similar", h.get("cls") or "") else 0,
        len(h["text"]),
    ))
    active = [h for h in (detail.get("hr") or [])
              if re.search(r"活跃|在线|刚刚|本周|今日|回复", h.get("text") or "")]
    active.sort(key=lambda h: (
        0 if re.search(r"active|online|status", h.get("cls") or "") else 1,
        len(h["text"]),
    ))
    if hr:
        proposed["hr_name"] = hr[0]["selector"]
    if active:
        proposed["hr_active"] = active[0]["selector"]
    if hr and active and hr[0]["selector"] == active[0]["selector"]:
        proposed["hr_name"] = hr[0].get("nth_selector") or hr[0]["selector"]
        proposed["hr_active"] = active[0].get("nth_selector") or active[0]["selector"]
        collisions.append(
            f"HR 名字与活跃标记共用选择器 {hr[0]['selector']}，已改用 nth 消歧"
        )

    chat = report.get("chat") or {}
    if chat.get("inputs"):
        best = sorted(chat["inputs"], key=lambda i: (i["tag"] != "textarea", not i.get("visible")))[0]
        proposed["greet_input"] = best["selector"]
    if chat.get("sends"):
        sends = sorted(chat["sends"], key=lambda s: (s["tag"] not in {"button", "a"}, len(s["selector"])))
        proposed["greet_send"] = sends[0]["selector"]

    # 探针覆盖不到的字段，明说而不是假装校准完了
    not_covered: dict[str, str] = {}
    if not proposed.get("headhunter_marker"):
        not_covered["headhunter_marker"] = (
            "页面上『猎头/中介』标记。不填 → is_headhunter 一直是未知，"
            "『排除猎头岗』规则不会拦下任何岗位（README 承诺的过滤会静默失效）"
        )
    if not proposed.get("card_link"):
        not_covered["card_link"] = (
            "卡片里指向详情页的 <a>。不填 → external_id 退化为 boss-N、jd_url 为空，"
            "抓详情会被直接跳过（JD/学历/HR 状态全空），去重也只能靠公司+岗位名"
        )
    if not proposed.get("login_marker"):
        not_covered["login_marker"] = (
            "只在未登录时存在的元素。不填 → ensure_login() 无法判断登录态，"
            "也不会识别『登录失效』这个熔断信号"
        )

    return {
        "proposed": proposed,
        "missing_for_search": [f for f in REQUIRED_FOR_SEARCH if not proposed.get(f)],
        "missing_for_send": [f for f in REQUIRED_FOR_SEND if not proposed.get(f)],
        "collisions": collisions,
        "not_covered": not_covered,
    }


def _print_report(report: dict) -> None:
    proposed = report.get("proposed") or {}
    if report.get("error"):
        print(f"\n!! 采集未成功：{report['error']}", flush=True)
    if report.get("warning"):
        print(f"\n⚠ {report['warning']}", flush=True)
    if report.get("rejected_proposal"):
        print(f"\n!! 已丢弃这次凑出来的『建议』（页面不可信，写进去会投错）："
              f"{report['rejected_proposal']}", flush=True)
    print("\n===== 建议写入的选择器（含证据） =====", flush=True)
    if not proposed:
        print("  （无：本次没有可信的采集结果）", flush=True)
    for key, sel in proposed.items():
        print(f"  {key:20s} = {sel}", flush=True)
    for note in report.get("collisions") or []:
        print(f"  ⚠ {note}", flush=True)
    print(f"\n  抓候选还缺: {report.get('missing_for_search') or '无'}", flush=True)
    print(f"  真投递还缺: {report.get('missing_for_send') or '无（招呼语阶段可能没做）'}", flush=True)
    if report.get("not_covered"):
        print("\n  探针没有覆盖、需要你确认的字段：", flush=True)
        for key, why in (report["not_covered"] or {}).items():
            print(f"    - {key}: {why}", flush=True)
    print("\n===== 逐张卡片实抽（照 browser.py 的取法）=====", flush=True)
    for row in (report.get("cards") or {}).get("per_card") or []:
        print(f"   {row}", flush=True)
    print(f"\n报告 -> {REPORT_PATH}", flush=True)
    print("确认无误后写配置：python scripts/apply_boss_probe.py --apply "
          "scripts/boss_selectors_proposed.json", flush=True)


# --------------------------------------------------------------------------- #
def run_apply(report_file: Path) -> int:
    """把报告里的选择器合并进平台配置（PUT 是全量覆盖，必须整份回传）。"""
    report = json.loads(Path(report_file).read_text(encoding="utf-8"))
    proposed = {k: v for k, v in (report.get("proposed") or {}).items() if v}
    if not proposed:
        print("报告里没有可写入的选择器")
        return 1

    with httpx.Client(base_url=API_BASE, timeout=60.0) as client:
        current = client.get("/api/apply/settings").raise_for_status().json()
        merged_selectors = {**(current.get("selectors") or {}), **proposed}
        payload = {**current, "platform": "boss", "selectors": merged_selectors}
        payload.pop("max_per_minute", None)
        payload.pop("max_per_day", None)
        saved = client.put("/api/apply/settings", json=payload).raise_for_status().json()
        print("已写入选择器：", json.dumps(proposed, ensure_ascii=False, indent=2))
        print("平台:", saved["platform"], "| enabled:", saved["enabled"], "| 风险确认:", saved["risk_acknowledged"])
        status = client.get("/api/apply/status").raise_for_status().json()
        print("adapter_ready =", status["adapter_ready"], "|", status["adapter_note"] or "就绪")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Boss 选择器自动校准探针")
    parser.add_argument("--apply", metavar="REPORT", help="把报告里的选择器写入平台配置（不开浏览器）")
    parser.add_argument("--check", action="store_true", help="只查这个 profile 的登录态（有头；本身也算一次自动访问，别反复跑）")
    parser.add_argument("--keyword", default="机器视觉", help="搜索关键词（默认 机器视觉）")
    #: 不能留空！`&city=` 会让 Boss 跳到它自己挑的城市（实测跳到许昌）并带 _security_check
    parser.add_argument("--city", default="101010100",
                        help="城市代码（默认 101010100=北京；留空会落到 Boss 自选城市）")
    parser.add_argument("--wait", type=int, default=240, help="等待登录/卡片出现的秒数（默认 240）")
    parser.add_argument("--greet-wait", type=int, default=150, help="等待你点开聊天框的秒数（默认 150）")
    parser.add_argument("--skip-detail", action="store_true", help="跳过详情页探测")
    parser.add_argument("--skip-greet", action="store_true", help="跳过招呼语阶段")
    parser.add_argument(
        "--allow-logged-out", action="store_true",
        help="允许在未登录页面上采列表页选择器（只为先把『抓候选』跑通；"
             "详情/打招呼仍必须登录后重新校准）",
    )
    args = parser.parse_args()

    if args.apply:
        return run_apply(Path(args.apply))
    if args.check:
        return asyncio.run(run_check(args))
    return asyncio.run(run_probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
