# Boss 适配器选择器校准指南

半自动投递的**队列、过滤、限速、熔断逻辑与平台无关**（已用 Mock 适配器端到端验证过），
唯一需要真机校准的是 `app/apply/adapters/selectors.py` 里的选择器——
也就是"页面上哪个元素是搜索框、哪个是岗位卡片"。

## 为什么选择器是空的

平台 DOM 改得很勤，写死一套选择器的结果通常是"某天突然抓不到岗位，还没有任何提示"。
所以这里的做法是：**选择器全部外置，未校准时明确报错**（错误里会点名缺哪个字段），
而不是编一套看起来能用、实际第一次跑就崩的选择器。

## 怎么取选择器

> 省事的办法：`scripts/apply_boss_probe.py`（patchright + 你已有的登录 profile）会自动打开页面、
> 跑 DOM 探测，把**候选选择器 + 命中数量 + 实测文本**一起列出来，再用和适配器**完全相同**的取法复核一遍；
> `--apply <报告>` 才写入配置（采集不成功时它会拒绝输出建议，不会把风控页的选择器写进配置）。
> 手工校准仍适用于它覆盖不到的字段。

1. Chrome 打开对应页面并登录，按 `F12` → Elements；
2. 用左上角的箭头点你要的元素；
3. 在 Elements 面板里右键该元素 → `Copy` → `Copy selector`；
4. 更稳的做法是**优先用稳定的属性**（`data-*`、`class` 里语义明确的部分），
   避开带随机哈希的 class（形如 `css-1x2y3z`，这种会随版本变）。

> 建议在"选择器"里尽量用 `[data-xxx="..."]`，比深层 `div > div > span:nth-child(3)` 稳得多。

## 需要校准的字段

| 字段 | 用途 | 在哪一页 |
| --- | --- | --- |
| `search_input` | 搜索框 | 职位搜索页 |
| `job_card` | 一张岗位卡片的容器 | 搜索结果列表 |
| `card_title` | 卡片里的岗位名 | 同上 |
| `card_company` | 卡片里的公司名 | 同上 |
| `card_salary` | 卡片里的薪资 | 同上 |
| `card_city` | 卡片里的城市/区域 | 同上 |
| `card_link` | 卡片里指向详情页的 `<a>` | 同上 |
| `detail_jd` | 详情页的 JD 正文 | 岗位详情页 |
| `detail_experience` | 经验要求 | 岗位详情页 |
| `detail_degree` | 学历要求 | 岗位详情页 |
| `hr_name` | HR 名字 | 岗位详情页 |
| `hr_active` | "近期活跃"之类的标记（**存在即视为活跃**） | 岗位详情页 |
| `headhunter_marker` | 猎头/中介标记（存在即视为猎头） | 岗位详情页 |
| `greet_button` | "立即沟通"按钮 | 岗位详情页 |
| `greet_input` | 招呼语输入框 | 弹出层 |
| `greet_send` | 发送按钮 | 弹出层 |
| `resume_send_button` | "发送简历"入口按钮 | 沟通页 / 弹出层 |
| `resume_upload_input` | 简历附件的 `input[type=file]`（通常被 CSS 藏起来） | 弹出层 |
| `resume_confirm_button` | 选完文件后的确认按钮 | 弹出层 |
| `resume_sent_marker` | 发送成功后出现的标记（**存在即视为成功**） | 沟通页 |
| `captcha_marker` | 验证码/滑块出现时的元素（**检测到就熔断**） | 任意页 |
| `login_marker` | 未登录时才存在的元素 | 任意页 |

> **`resume_*` 这四个才是"投递简历"真正需要的东西。** 在它们校准之前，点「投递简历」
> 会在发完招呼语之后**明确失败**并指出缺哪个选择器——不会安静地只发一句招呼语、
> 然后记成"已投递"（那是引入它们之前的实际行为：`send_greeting` 收下 `resume_image`
> 参数却从未使用过）。
>
> `resume_upload_input` 留空也能工作：那种情况下发的是**平台上的在线简历**，
> 结果里会写明这一点，不会让你以为附件发出去了。

`captcha_marker` 与 `login_marker` 尤其重要：没有它们，程序无法判断"账号是不是被平台警告了"，
`/api/apply/status` 会一直显示适配器未就绪并拒绝投递（**宁可拒绝，也不要在看不见状态的情况下乱投**）。

## 怎么填

> ⚠️ **`PUT /api/apply/settings` 是全量覆盖**（不是局部更新）。只有 `platform`、
> `default_channel`、`user_data_dir` 会在字段缺失时保留原值；`enabled`、`risk_acknowledged`、
> `want_salary_min`、限速参数和 **`selectors`** 都会按请求体重置。
> 所以正确的做法是：先 `GET` 拿到整份设置，改掉要改的字段，再整份 `PUT` 回去。
> （前端就是这么做的；命令行手改时最容易在这里把已校准的选择器清空。）

```powershell
# 先看当前设置（务必整份回传）
curl http://127.0.0.1:8000/api/apply/settings

# 把选择器填进去（只传改动的字段也可以，其余字段保持原值）
curl -X PUT http://127.0.0.1:8000/api/apply/settings `
  -H "Content-Type: application/json" `
  -d '{
        "enabled": true,
        "risk_acknowledged": true,
        "platform": "boss",
        "selectors": {
          "search_input": "input[name=\"query\"]",
          "job_card": ".job-card-wrapper",
          "card_title": ".job-name",
          "card_company": ".company-name",
          "card_salary": ".salary",
          "card_link": "a.job-card-left",
          "greet_button": ".btn-startchat",
          "greet_input": "#chat-input",
          "greet_send": ".btn-send",
          "captcha_marker": ".geetest_panel, .captcha-box",
          "login_marker": ".sign-in-btn"
        }
      }'
```

> 上面这些值是**格式示例，不是真实可用值**。请用你自己页面上取到的选择器。

## 验证顺序

```powershell
# 1. 状态：适配器是否就绪、还缺哪些字段
curl http://127.0.0.1:8000/api/apply/status

# 2. 抓候选（不会投递）：能抓到岗位说明搜索与卡片选择器对了
curl -X POST http://127.0.0.1:8000/api/apply/search -H "Content-Type: application/json" -d '{"keywords": ["后端"], "limit": 5}'

# 3. 抓详情（传 fetch_detail 才会进详情页补 JD/学历/HR 状态）
curl -X POST http://127.0.0.1:8000/api/apply/search -H "Content-Type: application/json" -d '{"keywords": ["后端"], "limit": 2, "fetch_detail": true}'

# 4. 真的投递前，先用演练模式确认一遍
curl -X POST http://127.0.0.1:8000/api/apply/queue/approve -H "Content-Type: application/json" -d '{"task_ids": [1]}'
curl -X POST http://127.0.0.1:8000/api/apply/execute -H "Content-Type: application/json" -d '{"limit": 1, "dry_run": true}'

# 5. 确认无误后再真投（建议第一次只投 1 条）
curl -X POST http://127.0.0.1:8000/api/apply/execute -H "Content-Type: application/json" -d '{"limit": 1}'
```

网页上做同样的事更直观：**投递执行台**标签 → 开启模块 → 抓取候选 → 勾选 → 确认投递。
第一次务必勾着"演练模式"跑一遍。

## 已经校准过的字段（Boss 列表页）

2026-09 在一台真实机器上用一次登录会话校准并复核过（脚本 `scripts/apply_boss_probe.py`）：

| 字段 | 值 | 验证方式 |
| --- | --- | --- |
| `search_input` | `input.input` | 页面计数 1 个可见元素，placeholder「搜索职位、公司」 |
| `job_card` | `ul.rec-job-list li.job-card-box` | 页面 15 个卡片、每个都含 `/job_detail/` 链接 |
| `card_title` | `a.job-name` | 真机快照离线复核 2/2 |
| `card_company` | `span.boss-name` | 同上（注意：`span.company-location` 是**地点**不是公司） |
| `card_salary` | `span.job-salary` | 同上（值被字体反爬，见下） |
| `card_city` | `span.company-location` | 同上 |
| `card_link` | `a.job-name` | 同上（相对路径，适配器已补成绝对地址） |

**仍未校准**：`detail_jd`、`detail_experience`、`detail_degree`、`hr_name`、`hr_active`、
`greet_button`、`greet_input`、`greet_send`、`captcha_marker`、`login_marker`、
`headhunter_marker`。抓候选不依赖它们，但**抓详情与真投递会**（`/status` 会明确报还缺哪些）。

参考项目 [loks666/get_jobs](https://github.com/loks666/get_jobs) 的 `Locators.java` 给出的候选值
（**别人的页面取值，务必自己复核后再填**）：

| 字段 | 候选（Playwright 可直接用） |
| --- | --- |
| `detail_jd` | `//div[@class='job-sec-text']` |
| `greet_button` | `[class*='btn btn-startchat']` |
| `greet_input` | `//div[@id='chat-input']` |
| `greet_send` | `//button[@type='send']` |
| `hr_active` | `//span[@class='boss-active-time']` |
| `hr_name` | `//div[@class='boss-info-attr']` |
| `login_marker` | `//a[@ka='403_login']`（登录失效页） |
| `detail_jd`（详情页薪资） | `//div[@class='info-primary']//span[@class='salary']` |

## 已知的平台怪癖（实测，2026-09）

1. **空 `city=` 参数会让 Boss 跳到它自选的城市**，而且**那个城市会被记进这个 profile**——
   之后每次不带城市抓取都跟着跑偏（我们实测它记住了"许昌"，Boss 的城市表接口里
   `locationCity` 也确实是许昌，即按 IP 定的）。
   所以城市是**持久化设置**（投递执行台 → 平台设置里的「城市」），
   **名字和代码都能填**："许昌" / "许昌市" / "101180400" 等价（`app/apply/cities.py` 用
   Boss 官方城市表 400 条做转换，数据文件 `city_codes.json`，重新生成：
   `scripts/refresh_city_codes.py`）。认不出的名字会**明确报 400**，不会静默搜错城市。
   单次搜索仍可用 API 的 `city` 字段覆盖；适配器也会把空参数丢掉，不会拼出 `city=`。
   清空该设置＝用平台当前城市（注意它跟 `enabled` 一样是**全量覆盖**语义，不是"保持原值"）。
2. **列表页的薪资数字被自定义字体替换成私用区码点**（实测 `U+E032 U+E033 - U+E032 U+E039 K · U+E032 U+E034 薪`），
   `parse_salary` 会**显式**返回 None，`搜索` 的 notes 里会提示「N 个岗位薪资无法解析」——
   也就是说**「薪资区间」过滤规则对这些岗位不生效**（不拦、也不误拦）。
   **但详情页的同一字段是正常文本**（实测 `12-18K·13薪`，不含 PUA），所以解法很直接：
   校准 `detail_salary`（`div.info-primary span.salary`）并在抓取时勾上**「抓详情」**
   （`fetch_detail=true`）——适配器会用详情页的薪资覆盖列表页那份被混淆的值，
   薪资规则随之恢复生效。代价：每条岗位多开一次详情页，更慢、**也更容易触发风控**
   （get_jobs 作者提到的"密集遍历被弹滑块"就是这个场景），建议同时把抓取数量调到 5–10。
   > 不建议照抄 get_jobs 的写死映射表（`U+E8F0..U+E8F9`→`0..9`）：实测码点区间**已轮换**，
   > 写死的表轮换后会把数字**解错**——比解不出更糟。要"永远正确"只有两条路：
   > 拦截平台自己的 JSON 接口（Boss 是 `/wapi/zpgeek/job/detail.json` 的 `salaryDesc`），
   > 或者用平台自己的薪资筛选条件，让平台来过滤。
3. **风控页识别**：`/web/user/safe/verify-slider`、`/web/passport/zp/verify`、
   `/web/passport/zp/security` 命中即熔断停投当天；`_security_check=` 标记**只在页面没有岗位内容时**
   才算风控（实测标记会挂在渲染正常的结果页上，只看标记会误熔断）。
4. **密集访问会触发滑块**（get_jobs 作者的实战记录，我们也复现了 `_security_check=1→5` 的嵌套循环）。
   所以校准探针不要反复跑；`--check` 只在需要时用一次。
5. **岗位链接是相对路径**（`/job_detail/xxx.html`），适配器已用 `urljoin` 补全；
   否则 `fetch_detail` 的 `page.goto()` 会直接失败。
6. **`--reload` 会让浏览器自动化直接 500**（Windows 专属，根因很隐蔽）：
   uvicorn 在 `reload=True` 或 `workers>1` 时用 **`SelectorEventLoop`**
   （`uvicorn/loops/asyncio.py`：`use_subprocess=True` → `SelectorEventLoop`），而它
   **不支持 asyncio 子进程**——`create_subprocess_exec()` 抛 `NotImplementedError`，
   于是 `POST /api/apply/search` 报 500。这里踩过：
   `scripts/dev.ps1` 永远带 `--reload`，所以这条链路在 Windows 上**从来没跑通过**。
   现在 `BrowserAdapter` 会把浏览器调用自动挪到"自带 Proactor 循环"的线程里执行
   （`isolated_browser_loop`），因此 `--reload` 下也能用；其它情况保持内联执行不变。
   回归测试：`tests/test_apply_platform_robustness.py::test_browser_call_is_isolated_from_selector_loop`。

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `/status` 一直显示"还有 N 个选择器未校准" | 字段没填全 | 按上表补齐 |
| 报"未安装 patchright" | 依赖没装 | `pip install patchright` 且 `patchright install chrome` |
| `POST /api/apply/search` 报 **500**，日志里是 `NotImplementedError` + `create_subprocess_exec` | Windows + `--reload` 用 SelectorEventLoop，建不了子进程 | 已自动隔离（见上「已知怪癖 6」）；若你改过适配器，确认浏览器调用走了 `isolated_browser_loop` |
| 搜索页打开就报"命中风控信号" | 检测到验证码/掉线 | 手动处理后再试，**当天不要继续自动投** |
| 抓到的岗位标题是空的 | `card_title` 选错了层级 | 用 DevTools 重新取 |
| 报"解析第 N 张卡片失败…页面结构变了" | 卡片选择器或子选择器失效 | 重新校准（这类错误**不会**触发熔断，因为是我们自己的适配问题） |
| 点击"立即沟通"没反应 | `greet_button` 选到了不可见的元素 | 换更具体的选择器；必要时用 `>> nth=0` |
| 登录态很快失效 | 用户数据目录被清 | 默认在 `data/browser/boss`，删了这个目录就要重新扫码 |

## 关于反检测

用的是 patchright + **真实 Chrome**（`channel="chrome"`）+ 持久化上下文 + 有头模式，
这是官方推荐组合（内置 Chromium 与无头模式反而更容易被识别）。
另外实现了人类化节奏：随机延迟、逐字输入、点击前后停顿。

本项目**不实现**验证码自动破解：出现验证码就意味着该停下来让人处理——这是设计意图，不是缺陷。
但"让程序继续"和"绕过验证码"是两件事，所以提供了**可选的等待策略**（检测到 → 暂停 → 通知用户手动完成 → 继续；
默认仍是熔断停投当天）。多账号轮换、代理 IP 池、指纹伪装同理：已实现、默认关闭、需逐级风险确认
（见 `apply/capabilities.py`）。
