# Boss 适配器选择器校准指南

半自动投递的**队列、过滤、限速、熔断逻辑与平台无关**（已用 Mock 适配器端到端验证过），
唯一需要真机校准的是 `app/apply/adapters/selectors.py` 里的选择器——
也就是"页面上哪个元素是搜索框、哪个是岗位卡片"。

## 为什么选择器是空的

平台 DOM 改得很勤，写死一套选择器的结果通常是"某天突然抓不到岗位，还没有任何提示"。
所以这里的做法是：**选择器全部外置，未校准时明确报错**（错误里会点名缺哪个字段），
而不是编一套看起来能用、实际第一次跑就崩的选择器。

## 怎么取选择器

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
| `captcha_marker` | 验证码/滑块出现时的元素（**检测到就熔断**） | 任意页 |
| `login_marker` | 未登录时才存在的元素 | 任意页 |

`captcha_marker` 与 `login_marker` 尤其重要：没有它们，程序无法判断"账号是不是被平台警告了"，
`/api/apply/status` 会一直显示适配器未就绪并拒绝投递（**宁可拒绝，也不要在看不见状态的情况下乱投**）。

## 怎么填

```powershell
# 先看当前设置
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

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `/status` 一直显示"还有 N 个选择器未校准" | 字段没填全 | 按上表补齐 |
| 报"未安装 patchright" | 依赖没装 | `pip install patchright` 且 `patchright install chrome` |
| 搜索页打开就报"命中风控信号" | 检测到验证码/掉线 | 手动处理后再试，**当天不要继续自动投** |
| 抓到的岗位标题是空的 | `card_title` 选错了层级 | 用 DevTools 重新取 |
| 报"解析第 N 张卡片失败…页面结构变了" | 卡片选择器或子选择器失效 | 重新校准（这类错误**不会**触发熔断，因为是我们自己的适配问题） |
| 点击"立即沟通"没反应 | `greet_button` 选到了不可见的元素 | 换更具体的选择器；必要时用 `>> nth=0` |
| 登录态很快失效 | 用户数据目录被清 | 默认在 `data/browser/boss`，删了这个目录就要重新扫码 |

## 关于反检测

用的是 patchright + **真实 Chrome**（`channel="chrome"`）+ 持久化上下文 + 有头模式，
这是官方推荐组合（内置 Chromium 与无头模式反而更容易被识别）。
另外实现了人类化节奏：随机延迟、逐字输入、点击前后停顿。

本项目**不实现**也不打算实现：验证码绕过、多账号轮换、代理 IP 池、指纹伪造。
出现验证码就意味着该停下来了——这是设计意图，不是缺陷。
