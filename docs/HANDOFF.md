# 交接说明（写给接手开发的 agent）

> 这份文档假设你**没有**参与之前的开发，但要在同一个仓库里继续做。
> 里面的每条"事实"我都在写之前核实过（提交历史、测试基线、目录结构、PLAN 定义），
> 不是凭记忆写的。**「已知的坑」那一节请先读完再动手**——那些都是真实踩过的。

---

## 一、这个项目是什么

**本地优先的求职操作系统**：模拟面试 → 评分与教学 → 薄弱点训练 → 简历与话术 → 半自动投递 → 投递分析 → 表达体检 → 压力面 → Offer 与谈判 → 人脉与学习。

- 后端：Python 3.14 + FastAPI + SQLModel + SQLite（`backend/`）
- 前端：React + TypeScript + Vite + Tailwind + recharts（`frontend/`）
- 浏览器扩展：MV3，抓自己账号的投递页（`extension/`）
- 设计文档：`docs/PLAN.md`（**唯一的权威规格**）
- 使用说明：`README.md`
- 平台选择器校准：`docs/APPLY_SELECTORS.md`

---

## 二、当前状态（写这份文档时核实过）

```
git log --oneline
025416b M14: 人脉与学习计划
ce123f9 M12: Offer 与决策
7dcf549 M11: 压力模式与多角色
9093e39 M9: 表达体检
7188637 M8: 简历与话术
196bf8d M10: 半自动投递
46dd2df M6: 投递数据
f47a447 M4: 节奏学习
a5340a6 M5: 薄弱点与训练
7db9f2b M3: 评分与教学
5ea1235 M1: 实时语音链路
0ae7195 M0: 文字模拟面试闭环
```

- 工作区干净（`git status --short` 无输出）
- **测试基线：`783 passed`**（48 个测试文件）
- 已完成：**M0 / M1 / M3 / M4 / M5 / M6 / M8 / M9 / M10 / M11 / M12 / M14 / M15** + **部署适配** + **M13 的算法题白板**
- 未完成：**M2**（题库与会话管理）、**M13 剩余两块**（系统设计白板 Excalidraw、群面多 agent 混音）

**M13 已经定下的几个决定（接着往下做时别推翻）**：

- **判题在浏览器端**（`frontend/src/sandbox/runner.ts`）：Web Worker + 整轮超时 `terminate()`
  + 屏蔽 `fetch`/`XHR`/`WebSocket`。**服务端永不执行用户或模型生成的代码**——本机没有
  docker，起不了隔离容器（PLAN §9.2 的原话）。前端只回传「哪一例过了 / 实际输出 / 报错」，
  `expected` 与 `note` 由后端从题库补：**判题事实的来源必须在后端**。
- 沙箱**不是安全沙箱**（Worker 与页面同源同权限），只防误伤与死循环。别在文档或 UI 里
  把它说成隔离环境。
- **题库是代码常量**（`backend/app/algo/problems.py`），不进 DB。M2 做 Alembic 时若需要
  用户自建题目，再把它搬进表——那时有明确对照物。
- 评估输出必须区分 `measured`（有测试结果支撑）与 `inferred`（模型从代码推断），
  缺省按 `inferred`（没证据时说得保守）。这是「不编造」在 M13 的落地方式。
- `AlgoProblemOut` **刻意不下发 `key_points`**（评分参照）。提前看到就把「你有没有想到」
  变成了「你抄得对不对」，而 M13 验收的正是前者。
- Monaco 从**本地**打包（`loader.config({ monaco })` + `?worker`），不走 CDN——项目定位是
  本地/内网可用。注意 0.56 的 `exports` 是 `"./*": "./esm/vs/*.js"`，子路径要写
  `monaco-editor/editor/editor.worker`，写 `monaco-editor/esm/vs/...` 会解析成
  `esm/vs/esm/vs/...`。

**部署形态变了**（重要，会影响你写的每个新接口）：用户要把项目放到 **Windows 云服务器**
上，选定「应用内单用户密码登录 + IP 自签证书 + 除投递执行与浏览器扩展外全部上云」。
这与 PLAN §10 第 1 条「本地优先、无需账号体系」冲突，**以用户的新选择为准**。
细节见 `docs/DEPLOY.md`，你需要知道的要点：

- `/api/*` 已由中间件统一鉴权（`app/auth.py` + `main.py`）。新增路由**不用**自己挂鉴权，
  但要记得：**免鉴权路径只有 `auth.PUBLIC_PATHS` 里那几个**，加新的要在那里显式登记。
- WebSocket 必须自己校验（`websocket_authenticated`），中间件管不到 WS。
- 自签证书意味着**只能在 HTTPS 或 localhost 下测语音**。
- 写「本机专属」的新功能（要复用本机浏览器/登录态的），挂 `app/deploy.py` 的
  `require_local_run` 依赖，云端会自动 403 并说明原因。

排期顺序不一定要按编号：M14 是在 M12 之后做的。看依赖与价值。

---

## 三、环境与命令（都核对过）

| 项 | 情况 |
| --- | --- |
| Python | 3.14.6；虚拟环境在 `backend/.venv` |
| 前端包管理 | pnpm 11 |
| 没有的 | java / javac / gradle / mvn / go / cargo / rustc / docker / make / rg |

**三条必须知道的命令细节**：

1. **一律用 `backend\.venv\Scripts\python.exe`**，不要依赖 PATH 里的 `python`
2. **`pnpm` 必须通过 `cmd /c` 调用**：PowerShell 执行策略会拦截 `pnpm.ps1`
3. 本机有系统代理 → `httpx` / `websockets` 默认 `trust_env=True` 会连不上国内接口；
   项目已默认 `TRUST_ENV_PROXY=false`，**你写探测脚本时要显式 `trust_env=False` / `proxy=None`**

```powershell
# 测试
cd backend; .\.venv\Scripts\python.exe -m pytest -q

# 只跑相关文件（开发中常用）
cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_xxx.py -q --tb=short

# 起后端（改代码后要重启，没开 --reload）
cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend; cmd /c "pnpm build"
cd frontend; cmd /c "pnpm dev"
```

> 起后端时建议给独立数据目录：`$env:DATA_DIR = Join-Path $env:TEMP "你的目录"`，
> 免得污染 `backend/data`。**注意后端进程会占住 SQLite 文件，删目录前先停进程**，
> 否则你会得到"数据没清掉、测试数据累积"的假象（这个坑踩过）。

---

## 四、架构约定（改代码前先读这一节）

### 1. `analysis/` 只放纯函数

不碰 DB、不碰网络、不读文件。所有副作用（读音频、查库、调模型）都在 `app/*_service.py` 里。

**这条规矩的价值**：造一条音量序列就能验证停顿检测、造一段对话就能验证谈判简报，
不需要起服务、不需要外部 Key。**请保持它。**

现有 25 个分析模块，命名与职责一目了然（`filters` / `scoring` / `rubric` / `pacing` /
`resume_match` / `ats` / `speech_features` / `offer_matrix` / `negotiation` / `networking` / …）。

### 2. `providers/` 是可插拔的，且 Mock 必须能跑通全链路

`llm/`（deepseek + mock）、`speech/`（qwen + mock）、`asr/`、`tts/`。
**Mock 不是占位符，是基础设施**：项目从 M0 起就靠"没有 Key 也能端到端跑通"来保证开发不被外部服务卡住。
新增 LLM 任务时，记得给 Mock 加一个分支（见 `providers/llm/mock.py`），并且
**Mock 要真的读输入**（比如 Mock 评分会看回答文本、Mock 招呼语会看 JD 关键词），
否则"这条链路被验证过"就是假的。

### 3. 编排层不写 prompt、不碰 DB 事务

`realtime/session_runtime.py` 只做协调；prompt 全在 `realtime/interviewer.py` 与 `analysis/`。
落库一律短事务：**绝不在 DB 事务里等模型**（本地 SQLite 单写者，长事务会把 HTTP 链路也拖住）。

### 4. 时间统一 `app.clock.utcnow()`

返回 **aware UTC**。SQLModel 0.0.46 的 `UTCDateTime` 拒绝 naive datetime。

### 5. `slots=True` 的 dataclass 没有 `__dict__`

转 Pydantic 时用 `dataclasses.asdict()`，不要写 `obj.__dict__`。
（这个坑踩过 4 次：M5、M6、M8、M9）

### 6. 加 JSON 字段，而不是加列

`score_report.dimension_scores`、`session.plan`、`job_posting.factors`、`learning_item.breakdown`
都是 JSON —— 加维度、加配置、加统计明细都不改表结构（PLAN §9.1 第 3 条的预留设计）。

### 7. 数据库没有 Alembic

`SQLModel.metadata.create_all` **不会给已有表加列**。所以：

- 加 JSON key → 可以
- **新建表** → 可以（`create_all` 会建）
- 改已有表的列 → 不行，需要先引入 Alembic（这是 M2 的一部分）

目前所有里程碑都靠前两条绕过了。

### 8. 命名冲突的既有处理

`analysis/selector.py` 里的 `select` 函数在导入时起了别名 `select_candidates`，
因为它会覆盖 sqlmodel 的 `select`。

---

## 五、工作流（每个里程碑都这么走，10 步）

1. **先读 `docs/PLAN.md` 里对应小节的验收标准**，再动手（验收标准就是任务定义）
2. 写纯函数 + 测试。**让测试抓 bug，再改实现**——很多真 bug 是靠"我写一条边界断言"发现的
3. service 编排（含 LLM 调用要 `log_llm_result` / `log_llm_error`）
4. API + schemas（放进已有的 `api/`、`schemas/` 模块，别每加一个功能就开一个 router）
5. 前端页面（复用已有页面的 Tailwind 风格；配置面板这类共用件抽到 `components/`）
6. `pytest -q` 全绿，且**总数不能比 617 少**
7. 写端到端冒烟脚本（**放系统临时目录，不要提交**），**必须真跑一遍**
8. `cmd /c "pnpm build"` 通过
9. 更新 `README.md`（进度表加一行 + 加一节「XXX 怎么用」）与 `docs/PLAN.md` 的进度行
10. `git commit`，然后汇报

**两条提交细节**：

- 提交信息用 `git commit -F <临时文件>`。**别用 `-m`**：信息里的 `->` 会被 PowerShell 当重定向符
- 提交前检查 `git diff --cached --name-only` 里没有 `node_modules|.venv|backend/data/`

**汇报要给真东西**：贴冒烟脚本的真实输出片段、说清哪些**没**验证过。
"测试全绿"不等于功能对；反过来，"没装浏览器所以 Boss 适配器没真机验证"这种话必须写出来。

---

## 六、硬性纪律（不要违反，这是产品定位的一部分）

| 纪律 | 含义 |
| --- | --- |
| **不做法律结论** | 合同/竞业只给"该问什么" + 常见坑，措辞避开"违法/无效"，明确写"非法律意见" |
| **公司研究不下结论** | 只给来源 + 原文摘录 + 未经验证标注；不做"这是骗子公司"的判断 |
| **不做全自动投递** | 必须人工确认（只有 `apply_task.status == "approved"` 才会投）；不实现验证码绕过、多账号轮换、代理 IP 池、指纹伪造 |
| **不编造事实** | 简历改写只给句式模板（数字留空并标注"不会替你编"）；敏感题答案留方括号空白；故事卡只从简历里抽 |
| **样本不足时明说** | 简历匹配分段、表达体检都要在样本少时标注"仅供参考"，而不是给个看起来很确定的数字 |
| **不加重量级依赖** | 目前后端依赖很少（FastAPI / SQLModel / httpx / websockets，patchright 可选）。引入前先想清楚，并且优先"手写 + 可测" |
| **不做匿名社区** | 需要云后端与多用户，与"本地优先"冲突 |

---

## 七、已知的坑（都真实踩过，建议先扫一眼）

| 现象 | 原因与处理 |
| --- | --- |
| 全量测试**卡住不返回** | `session_runtime` 里属性名写错导致 WS 异常 → 测试等超时。**分文件跑**定位，别等 |
| 在 `asyncio.run` 里用 `TestClient` **死锁** | TestClient 需要自己的事件循环。冒烟脚本里直接用 `session_scope()` 建数据 |
| 改完代码后行为没变 | `uvicorn` 没开 `--reload`，**改完必须重启后端** |
| 删了 `backend/data` 但数据还在 | 后端进程占着 SQLite 文件。先 kill 进程，或换个新的 `DATA_DIR` |
| `git commit -m` | 信息里的 `->` 被 PowerShell 当重定向 → 用 `-F <文件>` |
| 中文句子里的 ASCII 双引号 | `"他说"还能再让""` 会**截断字符串**。中文引号一律用「」 |
| 编辑时锚点选错 | 把 `def foo(...)` 这一行当锚点替换掉，函数体变成悬空代码 → 44 个测试 `NameError`。**锚点要包含足够的上下文** |
| `obj.__dict__` | slots dataclass 没有 `__dict__` → 用 `dataclasses.asdict()` |
| Pydantic 模型少字段 | `property`（如 `weighted` / `seconds`）不会被 `asdict()` 带走，要手动补 |
| 固定路径被 `/{id}` 抢走 | `/api/learning/summary` 会被 `/api/learning/{item_id}` 匹配 → 固定路径要么放前面，要么不带 `{id}` 前缀 |
| 金额/数字解析 | 「月薪 3.5 万，年薪 42 万」这类句子里，线索词要按**最近距离**判断，且要有距离上限（否则「月薪加年终…36 万」会把 36 万当成月薪） |
| 时间戳缺失 | 文字模式回合没有 `start_ms/end_ms` → 不能拿几毫秒去算"语速"，必须设下限并降级 |

---

## 八、上一轮做的事：M15 入职与长期 + 导出（已完成）

**已完成并提交，测试基线 717。** 下面是当初的建议拆分，以及实际落地时的差异——
**差异和坑比拆分本身更有用**，接手 M2 / M13 时可以直接复用。

### 实际落地与建议的差异

- 导出**没做 PDF**；Excel 用「CSV 打包成 ZIP」（双击即开，不引 openpyxl 这类重依赖）
- 导出板块最终是 **34 个**。接手时只覆盖 21 个，漏掉了节奏学习的录音/转写/面试官画像、
  题库与练习记录、投递过滤规则与黑名单、导入流水——**这些都是"不能重新生成"的数据**，
  导出少了它们就不叫全量备份。唯一**故意不导**的是 `app_setting` / `provider_config`
  （可能含 API Key）与 `llm_call`（上行的成本日志）
- Notion / 飞书：接口返回 **501 + 可读报错**，不写假实现（与 M1 对 `QwenFileTranscriber`
  的处理一致：没真凭证就不假装能同步）
- 前端放在「入职与长期」页，四个子面板：入职清单 / 试用期 OKR / 长期与薪资 / 数据导出

### 踩到的坑（直接复用）

- **"计划日期"要用 `date` 而不是 `datetime`**：用 datetime 存会引出「2027-06-30 是哪个
  时区的零点」这个无法回答的问题，而 SQLModel 的 `UTCDateTime` 会直接拒绝 naive 值
  （接手代码在这里挂了一个测试）
- 导出令牌要能**带在 URL 上**（`?token=xxx`）才能挂到 `<a href>` 直接下载；后端同时接受
  `X-Export-Token` 头与查询参数，比对用 `secrets.compare_digest`。令牌为空时**一律 401**
  并给出生成命令，而不是放行
- **「板块存在」和「数据真的进了导出」是两回事**：只断言板块 key 会漏掉查库 / join /
  字段映射的错。要为每个板块写"造数据 → 导出 → 断言那一行的字段"（见
  `tests/test_export_coverage.py`，13 个用例）

### 当初的建议拆分（供对照）

`docs/PLAN.md` §9.2 里的原文定义：

> **M15 入职与长期 + 导出集成**（3–4 天） | 入职清单（背调/体检/材料/离职交接/租房/落户）、
> 试用期 OKR、转正述职、长期规划（晋升/跳槽时机/薪资基准）；导出 Markdown/Excel/PDF、
> Notion/飞书、本地开放 API | 依赖 M6 + M12 | **验收：一键导出全量数据与报告；入职清单可勾选追踪**

### 建议的拆分（按依赖与价值排序）

**第一波：导出（用户最需要，也最独立）**

- `analysis/export.py` 纯函数：把各表的数据转成**结构化中间格式**（Markdown 表格 / 多 sheet / JSON）
  - 关键设计：**先转成中间结构再渲染**，这样"导出内容完整"可以被单测，
    而不用去解析渲染出来的 Markdown
- 导出范围要**覆盖全部已有数据**：投递记录、面试会话与逐轮问答、评分报告与改进项、
    薄弱点与掌握度、训练记录、简历与版本、故事卡与话术、Offer 与谈判、人脉与跟进、学习清单
- 本地开放 API：`/api/export/*`，**要带 token 校验**（本地也不能裸奔，浏览器扩展同源问题另说）

**第二波：入职清单**

- `models/career.py`：`onboarding_task`（可勾选、带"为什么需要 + 去哪里办"）、`career_plan`
- 背调材料 / 体检 / 学历与离职证明 / 社保转移 / 离职交接 / 租房与落户
- **验收点：可勾选追踪**，所以要能在 UI 上勾、能看进度

**第三波：OKR 与长期**

- 试用期 OKR（目标 + 关键结果 + 进展），转正述职（把目标和进展合成一份稿）
- 长期规划：晋升/跳槽时机、薪资基准 —— **薪资基准只给参考区间与来源**，
  不要给"你应该要 40 万"这种结论（与 §六 的纪律一致）

### 不要做的事

- 不要为了导出引入 pandas / openpyxl 这类重依赖，先考虑"CSV 就是 Excel 能打开的"
- 不要做 PDF 生成这种重量级能力（除非用户明确要求）；Markdown + CSV + JSON 已经覆盖绝大多数场景
- Notion/飞书集成属于"可选"，**如果没有真实 token 就没法验证** → 要么留接口 + 可读报错，
  要么明确不做（参考 M1 对 `QwenFileTranscriber` 的处理：只留接口 + 可读报错，不假装实现）

---

## 九、怎么自证"做对了"

1. **测试基线不能降**（当前 717，45 个测试文件）
2. **端到端冒烟要有真输出**，而且要覆盖这一轮的新功能（不是重跑别人的）
3. **前端 `pnpm build` 通过**（tsc 无错）
4. 汇报里明确区分：
   - 「我验证过什么」（贴输出）
   - 「我没验证什么、为什么」（比如需要真实 Key、需要浏览器、需要云服务）
5. 不要为了让测试变绿而改测试的断言——**除非**确认是行为的有意变更，
   那就在测试注释里写清"为什么现在应该这样"

---

## 十、如果你要选 M2 或 M13

- **M2（题库与会话管理）**：题库 CRUD、按 JD 生成题、回合文本精修、**Alembic 迁移**。
  它是唯一剩下的"主干"工程债；Alembic 一旦引入，之后改表结构就不再有顾虑。
- **M13（硬核技术面）**：算法题白板（Monaco + 浏览器端判题沙箱）、系统设计白板、群面多 agent。
  工程量最大，且判题沙箱涉及安全边界，需要单独想清楚（别在本地跑不可信代码）。

不管选哪个，**先读 PLAN 里对应小节的验收标准**，那才是任务定义。
