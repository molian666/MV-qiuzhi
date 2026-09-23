# mianshi · 求职操作系统

本地优先的求职操作系统：**面试模拟与复盘 → 评分与纠错 → 薄弱点训练 → 投递分析与半自动投递 → Offer 决策**。
完整方案、数据模型与路线图见 [`docs/PLAN.md`](docs/PLAN.md)。

## 当前进度

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| **M0 骨架与文字闭环** | ✅ | 文字模拟面试端到端（岗位/简历 → 多轮问答 → 自动收尾 → 落库）；LLM 提供方可插拔（Mock / DeepSeek）；用量流水落库 |
| **M1 实时语音链路** | ✅ | WebSocket 双向音频、能量 VAD + 回合控制器、打断（barge-in）、录音存档（整场 + 逐回合 WAV）与回放、百炼实时 ASR provider（协议已实现，待真机校准） |
| **M3 评分与教学** | ✅ | 七维 Rubric（每项必须引原话作证据）+ 改进项（问题 / 证据 / 怎么改 / 用你经历重写的示范答案 / 立刻可做的练习）+ 报告页雷达图；Mock 模式也能出报告 |
| **M5 薄弱点与训练** | ✅ | 报告标签 → 掌握度（可解释的变化历史）+ 高危标记 + SM-2 间隔复习 + 加权抽题（薄弱度×岗位匹配×到期×题型平衡）+ 今日训练（3 薄弱 + 1 随机，作答即批改） |
| M2 题库与会话管理 | ⏭ 下一步 | 题库 CRUD、按 JD 生成题、回合文本精修、Alembic 迁移 |
| M4–M15 | 计划中 | 节奏学习、半自动投递、Offer 决策…… 见 PLAN §9 |

测试：**132 个 pytest**，覆盖文字 / 语音 / 评分 / 训练四条链路（会话生命周期、VAD、回合控制、音频存档、WebSocket、Rubric、评分规范化、报告 API、Mock 评分、掌握度、SM-2、加权抽题、训练 API）。

## 环境要求

- Python 3.11+（本机 Python 3.14.6 实测通过）
- Node 20+ 与 pnpm
- **无需任何 API Key**：默认 `LLM_PROVIDER=mock`，全流程可离线跑通

## 快速开始

### 1. 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env          # 需要真实模型时再改里面的配置
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- 接口文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

### 2. 前端

```powershell
cd frontend
pnpm install
pnpm dev                              # http://127.0.0.1:5173
```

前端不直连端口：`/api` 由 Vite 代理到 `127.0.0.1:8000`（见 `frontend/vite.config.ts`）。

### 3. 一键启动（可选）

```powershell
.\scripts\dev.ps1              # 后端 + 前端
.\scripts\dev.ps1 backend      # 只起后端
```

> Windows 上若 `pnpm` 被 PowerShell 执行策略拦住（`无法加载文件 pnpm.ps1`），
> 用 `cmd /c pnpm ...`，或把执行策略放开：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

## 语音面试怎么用

前端 →「语音面试」标签：

1. 选岗位/简历（可留空）→ 题量 →「开始语音面试」（创建 realtime 会话并连上 WebSocket）；
2. 点「开始说话」授权麦克风，正常说话；面试官的问题会用浏览器朗读出来；
3. 你开口时面试官的声音会自动停掉（打断），也可以点「打断面试官」按钮；
4. 说完自动进入下一题；点「结束并保存」结束，录音可在「历史面试」里整场或逐回合回放。

**两种语音模式**（`backend/.env`）：

| 配置 | 行为 |
|------|------|
| `SPEECH_PROVIDER=mock`（默认） | 不需要任何 Key。音频照常采集、录制、存档、回放；"音频→文字"这一步由你输入的文本替代——页面会明确显示「转写：文字替身」，**不会假装已经识别了语音** |
| `SPEECH_PROVIDER=qwen` + `DASHSCOPE_API_KEY=...` | 阿里云百炼实时语音识别（`qwen3-asr-flash-realtime`），说话即转写 |

面试官的"说话"目前用浏览器 `speechSynthesis` 播报（`audio_mode=client`）：零依赖、中文音色开箱可用，
让 M1 在没有服务端 TTS 的情况下也能真的听见对话。服务端 TTS 的协议位已留好
（`app/providers/tts/base.py`），接入后把 `audio_mode` 切到 `server`，音频改由 `audio_chunk` 事件下发，
前端播放器不用改。

> ⚠️ 如果你的机器开着系统代理（VPN / Clash 等），httpx 与 websockets 会尝试走代理而连不上国内接口。
> 本项目默认 `TRUST_ENV_PROXY=false` 显式绕过系统代理；确实必须经代理访问时才设为 true。

## 报告怎么用

面试结束后，顶部「报告」标签 → 选一场面试：

1. 没有报告时**自动生成**（`POST /report`）；已有报告直接复用，不会重复烧 token；
2. 报告包含：总分（0–100）、七维雷达图、整体评价、逐条改进项；
3. 每条改进项都带：**原话证据**（可点「回放这段」直接听音频）、怎么改、用你自己经历重写的示范答案、
   立刻可做的练习、能力标签（M5 会用这些标签做针对性训练）。

评分维度写死在 `app/analysis/rubric.py`（保证同一场面试多次评分**可比**，否则趋势图没意义）：

> 切题度 / 结构 / 信息密度与量化 / 技术深度与正确性 / 表达与流畅 / 时间控制 / 主动性

模型分工：评分与纠错走 `deepseek-reasoner`（质量优先），实时对话走 `deepseek-chat`（延迟优先）——
按调用任务（`task`）自动选择，见 `app/providers/llm/deepseek.py`。

接口：`POST /api/sessions/{id}/report?force=true|false`、`GET /api/sessions/{id}/report`、`GET /api/reports`。

## 训练怎么用

生成面试报告之后，去「训练」标签：

1. 顶部是总览：平均掌握度、高危薄弱点、待复习数量、能力标签数；
2. 「今日训练」默认给 **3 道薄弱点题 + 1 道随机题**；每题都能展开「为什么推荐这题」看到打分依据；
3. 点「开始作答」写答案 → 提交后立刻批改（分数 / 一句话点评 / 示范 / **掌握度变化**）；
4. 右侧「能力掌握度」按薄弱程度排序，点任意一项看它的**掌握度曲线**——
   每次变化都能追溯到来源（报告 or 训练）与当时得分。

**掌握度怎么算**（能手算复现，不做黑箱）：

```
new = old + 0.3 × (目标 − old)      目标 = (得分 − 1) / 4 × 100
首次观察直接取目标值（否则会被 0 起点稀释，用户觉得"练了没反应"）
```

- 低于 40，或最近连续两次都落在低分区 → 标记为**高危薄弱点**；
- 复习节奏用标准 SM-2：答对依次 1 天 → 6 天 → n×ease；答错回到 1 天重来。

**题目从哪来**：优先用题库里已覆盖薄弱标签的题；没有就调用模型现场生成并**写进题库**
（下次直接复用）——所以 M5 不依赖题库功能也能自成闭环。入库与组题都会按题干去重。

## 跑测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

测试用临时数据目录 + Mock 模型，不依赖网络、不碰真实数据。

## 切换到真实模型（DeepSeek）

编辑 `backend/.env`：

```
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

- 实时对话/追问走 `deepseek-chat`（延迟优先）
- 评分、纠错、简历诊断走 `deepseek-reasoner`（质量优先）

模型按调用任务（`task`）自动选择，见 `backend/app/providers/llm/deepseek.py` 的 `REASONER_TASKS`。

## 数据与隐私

- 全部数据在 `backend/data/`（SQLite、后续的音频与上传文件），已在 `.gitignore` 中。
- API Key 只存在 `backend/.env`：不入库、不返回前端、不写日志。
- 本地运行，除你自己配置的模型/语音服务外不向任何第三方发送数据。

## 目录结构

```
backend/
  app/
    api/         sessions.py targets.py resumes.py questions.py system.py   # REST 路由
    models/      core.py session.py scoring.py system.py                    # 表定义
    schemas/     对外契约（pydantic）
    realtime/    interviewer.py                                            # 面试官编排（M1 复用）
    providers/   llm/(base|deepseek|mock)                                   # 可插拔提供方
    clock.py db.py config.py deps.py services.py main.py
  tests/         会话生命周期、provider 单测
frontend/
  src/
    pages/       InterviewLive.tsx Sessions.tsx System.tsx
    api/         client.ts types.ts
docs/PLAN.md     方案、数据模型、里程碑、风险与决策记录
scripts/dev.ps1  本地启动脚本
```

## 设计约定（写代码前先读）

1. **每次 LLM 调用必须带 `task` 标签**，并落 `llm_call` 表（成本与效果对比靠它）。
2. **`analysis/` 下的 AI 功能写成纯函数 + JSON schema 输出**，便于复用与测试。
3. **时间统一 `app.clock.utcnow()`**（aware UTC）——SQLModel 0.0.46 的 `UTCDateTime` 拒绝 naive 值。
4. **编排层不碰数据库**：`realtime/interviewer.py` 只决定"问什么"，落库与传输在 api 层。
5. 新功能先看 `docs/PLAN.md` §9.1 的预留约束，避免后续返工。
