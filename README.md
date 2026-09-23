# mianshi · 求职操作系统

本地优先的求职操作系统：**面试模拟与复盘 → 评分与纠错 → 薄弱点训练 → 投递分析与半自动投递 → Offer 决策**。
完整方案、数据模型与路线图见 [`docs/PLAN.md`](docs/PLAN.md)。

## 当前进度

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| **M0 骨架与文字闭环** | ✅ 已完成 | 文字模拟面试端到端（岗位/简历 → 多轮问答 → 自动收尾 → 落库）；LLM 提供方可插拔（Mock / DeepSeek）；LLM 用量流水落库；16 个 pytest 通过 |
| M1 实时语音链路 | ⏭ 下一步 | 阿里云百炼 Qwen 实时语音（cascade：ASR → DeepSeek → TTS）、VAD、打断、录音回放 |
| M2–M15 | 计划中 | 题库与会话管理、评分与教学、节奏学习、半自动投递、Offer 决策…… 见 PLAN §9 |

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
