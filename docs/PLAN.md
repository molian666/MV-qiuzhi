# 求职操作系统（mianshi）实现方案与计划

> 状态：v0.5 已定稿（关键决策见 §12；扩展路线图见 §9.2；get_jobs 融合见 §6.7；技术路线定稿见 §3）｜ 形态：本地优先 Web 应用 ｜ 后端 Python (FastAPI) ｜ 前端 React + TS
> 语音：阿里云百炼「Qwen 实时语音（听/说）」+ DeepSeek（思考/出题/批改）
> 定位：本地优先的**求职操作系统**——定位 → 简历 → 投递 → 面试 → Offer → 入职

---

## 1. 目标与范围

从「面试教练」升级为**本地优先的求职操作系统**，覆盖完整求职漏斗：

```
定位/简历 ──► 投递(半自动) ──► 面试(模拟+复盘) ──► Offer 决策 ──► 入职/长期
   ↑ JD 匹配打分   ↑ AI 筛选+草拟   ↑ 评分/纠错/薄弱点   ↑ 对比矩阵+谈判模拟   ↑ 清单+述职
   └──────── 数据回流：投递漏斗 → 拒信归因 → 反向优化简历与训练重点 ────────┘
                    ↑
        真实面试官节奏学习 ←── 上传真实面试录音 ──┘
```

范围边界（先明确不做什么，避免失控）：

- 不做多租户 SaaS 与账号体系，单用户本机运行，数据全部落本地。
- **不做全自动无人值守投递**：只在「AI 筛选 + 草拟招呼语 → 用户确认 → 限速投递」的半自动模式内工作，模块默认关闭（见 §6.7 风控清单）。
- 不做匿名社区/面经共享（需要云后端与多用户，与本地优先冲突）：面经只做本地导入与自用题库。
- 不做法律结论：合同/竞业只做**条款提醒清单**；公司研究只给**来源链接 + 原文摘录 + 未经验证标注**，禁止结论性判断（见 §6.9）。
- 不做本地大模型推理（规避本机 Python 3.14 的 ML 依赖问题，统一走 API）。
- 语音模型与 LLM 供应商**可插拔**，任何一家挂了能切备用，且没有 Key 时能用 Mock 跑通全流程。

---

## 2. 需求拆解到模块

| # | 需求 | 模块 | 优先级 |
|---|------|------|--------|
| # | 需求 | 模块 | 优先级 |
|---|------|------|--------|
| 1 | 语音/文字多方式模拟面试 | `realtime` + `providers/speech` | P0 |
| 2 | 学习面试官的面试节奏 | `analysis/pacing` 节奏画像 | P1 |
| 3 | 按题库 + 学到的节奏提问 | `question_bank` + `pacing` | P0 |
| 4 | 面试后评分、指出问题、纠正并教答法 | `analysis/scoring` + `correction` | P0 |
| 5 | 记录薄弱点，下次针对薄弱点训练（错题本 + 间隔复习） | `learning/mastery` + 抽题加权 | P1 |
| 6 | 上传自己的面试录音，给问题与改进意见 | `recordings` 流水线 | P1 |
| 7 | 投递数据看板与漏斗分析 | `applications` + `analytics` | P1 |
| 8 | 各招聘平台投递数据同步 | `sync/adapters`（CSV / 扩展抓取） | P2 |
| 9 | 语音 + 文字双模态切换 | 前端 `audio/` + WS 协议 | P0 |
| 10 | **半自动投递助手（融合 get_jobs 能力）**：岗位抓取、过滤链、AI 打招呼语、图片简历、黑名单、确认后限速投递 | `apply/` + `providers/browser` | P2（M10） |
| 11 | 简历-JD 匹配诊断、**ATS 关键词优化**、自我介绍与敏感题话术 | `analysis/resume_match` | P2（M8） |
| 12 | 简历多版本管理与投递 A/B 效果统计 | `resume` 多版本 + `applications` | P2（M8/M10） |
| 13 | STAR 故事素材库 + 标准回答库 | `story_card` | P2（M8） |
| 14 | 表达表现力分析（填充词/语速/停顿/紧张度/视频仪态） | `analysis/audio_features` | P3（M9） |
| 15 | 面试官风格库 + 问题预测与追问树 | `interviewer_profile` 预设 + `pacing` | P3（M11） |
| 16 | 压力模式、多角色串联、英文面试 | `realtime` + 多画像 | P3（M11） |
| 17 | 算法/系统设计白板、群面模拟 | 前端白板 + 判题沙箱 | P3（M13） |
| 18 | **Offer 对比矩阵 + 薪资谈判模拟** | `offer` + 谈判角色画像 | P2（M12） |
| 19 | 公司研究与避坑（虚假招聘/培训贷/外包） | `analysis/company_research` | P3（M12） |
| 20 | 合同与竞业条款提醒（非法律意见） | `analysis/contract_check` | P3（M12） |
| 21 | 内推与人脉 CRM（联系人、沟通记录、跟进提醒） | `contacts` | P3（M14） |
| 22 | 面试日程 + AI 准备包 + 感谢信/跟进话术 | `analysis/prep_pack` + `applications` | P2（M10） |
| 23 | 技能差距 → 学习计划与完成度追踪 | `learning/path` | P3（M14） |
| 24 | 入职准备清单、试用期 OKR、转正述职、长期规划 | `career/` | P3（M15） |
| 25 | 拒信归因、JD 关键词热度、行业技能雷达 | `analytics` | P2（M10/M12） |
| 26 | 导出与集成（Markdown/Excel/PDF、Notion/飞书、本地开放 API） | `export/` | P3（M15） |

---

## 3. 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 后端框架 | FastAPI + uvicorn | 原生 async + WebSocket，适合实时音频双向流 |
| ORM / DB | SQLModel (SQLAlchemy 2.0) + SQLite | 单机零运维；本地文件即数据 |
| 数据校验 | Pydantic v2 | FastAPI 自带 |
| 配置 | pydantic-settings + `.env` | Key 本地保存，不入库明文（见 §11） |
| 异步任务 | 进程内 asyncio 任务队列（后续可换 SQLite 队列表） | 上传录音分析是长任务，先不做 Redis/Celery |
| LLM | DeepSeek `deepseek-chat` / `deepseek-reasoner`（OpenAI 兼容 SDK） | 中文面试题与批改质量好、成本低；reasoner 用于评分与纠错 |
| 实时语音 | 阿里云百炼（已定）：`qwen3-asr-flash-realtime`(听) + `qwen3-tts-flash`(说)，或 `qwen-omni-*-realtime` 端到端；线协议为 OpenAI Realtime 方言 | 中文实时语音质量好、国内可直连；协议差异由 adapter 吸收；DeepSeek 掌握出题与追问 |
| 批量 ASR | 云端文件转写（长音频）+ 可选本地备选 | 用于「上传录音分析」与面试后精修转写 |
| 前端 | Vite + React + TypeScript + Tailwind + TanStack Query + zustand | 快速搭 UI，状态边界清晰 |
| 音频采集 | `getUserMedia` + `AudioWorklet` → PCM16 16k 单声道分片 | 与实时语音 API 的输入格式一致，避免前端重采样库 |
| 图表 | Recharts | 评分雷达图、投递漏斗、掌握度曲线 |
| 浏览器自动化 | **patchright**（Python 官方包，`pip install patchright`，import 替换即用）→ 退化用 `playwright`；配「真实 Chrome + `launch_persistent_context` + 有头模式 + 不注入指纹」 | **已核实**：Patchright 官方只出 Python/Node/.NET，**无 Java 版**——get_jobs 是在 Java 侧手动装配 patchright-core 的 Node driver（`-Dplaywright.cli.dir` + `PLAYWRIGHT_NODEJS_PATH`）才拿到反检测能力；Python 侧用官方包即可，无需这层仪式。该系列跟随 Playwright 上游升级，**必须锁版本** |
| 简历 PDF→图片 | **浏览器端** pdf.js 渲染 canvas → JPG（不出本机） | 规避 Python 3.14 上 PyMuPDF/Pillow 的 wheel 风险，简历也不上传服务端 |
| 测试 | pytest + httpx（后端）、Vitest（前端） | 评分/节奏/掌握度这些纯逻辑必须先有测试 |

**为什么不是本地 Whisper**：本机为 Python 3.14.6，`torch` / `faster-whisper` 等 wheel 尚未稳定覆盖，走了会踩坑；且实时语音天然需要云 API。

---

## 4. 系统架构

```text
┌──────────────────────── 浏览器 (React + TS) ────────────────────────┐
│ 面试间   报告   录音上传   题库   投递执行台   投递分析   人脉   Offer 设置 │
│  ├ AudioWorklet 麦克风采集 / PCM 播放队列 / 波形                     │
│  └ REST(httpx/fetch) + WebSocket(双向音频与事件)                     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  /api/*   /ws/sessions/{id}
┌───────────────────────────────▼─────────────────────────────────────┐
│ FastAPI                                                             │
│  api/          路由：sessions · recordings · questions · targets     │
│                      applications · offers · contacts · settings     │
│  realtime/     会话编排：TurnController · PacingEngine · VAD · 打断  │
│  analysis/     节奏画像 · 评分 Rubric · 纠错与示范回答 · 音频特征    │
│  learning/     掌握度模型 · 抽题加权 · 复习计划(SM-2) · 学习路径     │
│  apply/        半自动投递：岗位抓取 · 过滤链 · 招呼语 · 待投队列     │
│                      · 限速器 · 熔断 · 图片简历（全程人工确认）      │
│  sync/         渠道适配：CSV 导入 · 浏览器扩展 ingest（IMAP 预留位）   │
│  providers/    llm / speech_realtime / asr / tts / browser ← 可插拔  │
└───────┬───────────────────────────────┬─────────────────────────────┘
        │ SQLModel                      │ httpx / websockets / patchright
┌───────▼─────────────┐        ┌────────▼──────────────────────────────┐
│ SQLite + 本地文件    │        │ DeepSeek · 实时语音厂商 · 批量 ASR      │
│ data/app.db          │        │ 招聘平台（经本机浏览器，半自动）        │
│ data/audio/{sid}/*   │        └───────────────────────────────────────┘
│ data/browser/（登录态）│
└──────────────────────┘
```

### 4.1 目录结构

```text
mianshi/
  backend/
    app/
      main.py  config.py  db.py  deps.py
      models/        # SQLModel 表定义
      schemas/       # 请求/响应 DTO
      api/           # sessions.py recordings.py questions.py applications.py skills.py settings.py
      realtime/      # session_manager.py turn_controller.py pacing.py vad.py events.py
      providers/
        llm/         base.py deepseek.py mock.py
        speech/      base.py qwen_realtime.py doubao_realtime.py mock_realtime.py
        asr/         base.py cloud_batch.py
        tts/         base.py edge_or_cloud.py
      analysis/      pacing_extract.py rubric.py scorer.py correction.py audio_features.py resume_match.py
      learning/      mastery.py selector.py review.py path.py
      apply/         adapters/(base.py boss.py liepin.py) filters.py say_hi.py queue.py
                     runner.py throttler.py breaker.py resume_image.py   # 半自动投递，见 §6.7
      sync/          csv_importer.py extension_ingest.py  # 另有 imap_mail.py 预留位（本次不实现）
    data/            # app.db / audio / uploads / browser 登录态（gitignore）
    tests/
    pyproject.toml
  frontend/
    src/
      pages/         InterviewLive Report Upload Recordings Questions ApplyDesk Applications
                     Analytics Contacts Offers Skills Settings
      components/    RecorderPanel WaveformView ConversationStream ScoreRadar FunnelChart
      audio/         micCapture.ts player.ts worklet/pcm-processor.js
      api/           client.ts ws.ts types.ts
    vite.config.ts  tailwind.config.js  package.json
  extension/         # 浏览器扩展：抓取已登录账号的投递页（M10）
  docs/PLAN.md  docs/ARCHITECTURE.md  docs/PROVIDERS.md
  .env.example   README.md   .gitignore
```

---

## 5. 数据模型（SQLite）

会话与音频：

- `interview_session`：`id, created_at, mode(realtime|text), target_id, resume_id, interviewer_profile_id, status(created|live|finished|scored), started_at, ended_at, duration_ms, provider, cost_estimate`
- `session_turn`：`id, session_id, idx, speaker(interviewer|candidate|system), text, start_ms, end_ms, audio_path, is_followup, question_id, asr_confidence`
- `audio_asset`：`id, session_id|recording_id, path, kind(full|turn), sample_rate, duration_ms`

题库与岗位：

- `job_target`：`id, company, title, jd_text, level, tags, created_at`
- `resume`：`id, name, content_text, version, variant_label, target_job_id, parent_resume_id, ats_report(json), created_at`（多版本 A/B 用 `variant_label` + `parent_resume_id`）
- `question_bank`：`id, text, type(self_intro|project_deep|behavior|algorithm|design|scenario|salary|reverse), tags(json), difficulty(1-5), reference_points(json), sample_answer, source(manual|uploaded|generated)`
- `question_attempt`：`id, question_id, session_id, answer_text, scores(json), asked_at`

节奏画像：

- `interviewer_profile`：`id, name, source, created_at, style_tags, params(json), notes`
  - `params` 见 §6.2
- `pacing_sample`：`id, profile_id, recording_id, question_type, ask_duration_ms, wait_ms, followup_count, interviewer_wpm, candidate_ratio, interrupt`

评分与提升：

- `score_report`：`id, session_id|recording_id, overall(0-100), dimension_scores(json), summary, generated_at, model`
- `improvement_item`：`id, report_id, severity, dimension, problem, evidence_turn_ids(json), suggestion, corrected_answer, drill`
- `skill_topic`：`id, name, parent_id, category`（标签树：技能点 / 问题类型 / 软技能）
- `skill_mastery`：`id, topic_id, mastery(0-100), attempts, correct_hits, updated_at, last_seen_at`
- `review_task`：`id, topic_id, question_id, due_at, interval_days, ease`（SM-2）

投递与投递执行（漏斗的执行端）：

- `job_posting`（**抓到的岗位，与 application 分离**）：`id, platform, external_id, company, title, jd_text, jd_url, salary_range, city, experience_req, degree_req, company_size, company_stage, industry, hr_name, hr_active, is_headhunter, matched_score, filter_verdict, fetched_at, tags(json)`
- `application`（我投的）：`id, job_posting_id, company, title, channel(boss|liepin|zhilian|51job|lagou|referral|other), jd_url, jd_text, resume_version, applied_at, status(applied|viewed|screening|interview1|interview2|hr|offer|rejected|ghosted|withdrawn), salary_range, city, source(manual|csv|extension|apply_bot), external_id, note`
- `application_event`：`id, application_id, at, from_status, to_status, raw_ref`
- `apply_rule`：`id, platform, name, kind(salary|experience|degree|company_size|company_stage|industry|keyword|headhunter|hr_active|dedup|blacklist), enabled, priority, params(json)`（过滤链，UI 可开关调参）
- `apply_task`：`id, job_posting_id, status(pending|approved|sent|skipped|failed|blocked), say_hi_text, resume_image_path, created_at, approved_at, sent_at, attempts, error`
- `platform_account`：`id, platform, login_state_path, last_login_at, daily_sent, daily_limit, circuit_broken_until, note`（熔断状态见 §6.7）
- `blacklist`：`id, platform, company, job_title, hr_name, reason(auto_rejected|manual|unresponsive), added_at`
- `import_batch`：`id, channel, source(csv|extension|imap|manual|apply_bot), at, rows, dedup_key_hash`

Offer 与决策：

- `offer`：`id, application_id, company, title, base_salary, months, bonus, equity, benefits(json), city, commute_min, level, growth_note, stability_note, deadline, raw_text`
- `offer_score`：`id, offer_id, criteria(json), weights(json), total, note`（加权对比矩阵）
- `negotiation_session`：`id, offer_id, target_range(json), walk_away, tradeables(json), plan_md, mock_session_id`（谈判简报 + 挂载的模拟会话）

人脉与准备：

- `contact`：`id, name, company, role, kind(referral|alumni|hr|interviewer), platform, wechat, email, phone, first_met_at, last_contact_at, note`
- `contact_event`：`id, contact_id, at, channel, summary, next_action_at`
- `prep_pack`：`id, application_id, company_card(json), predicted_questions(json), my_matches(json), questions_to_ask(json), sources(json), generated_at`（`sources` 强制非空：避坑内容必须带来源）
- `learning_path` / `learning_task`：`id, topic_id, title, steps(json), status, progress` ／ `id, path_id, title, due_at, done_at`
- `onboarding_task`：`id, application_id, kind(background_check|medical|materials|handover|housing|hukou|okr|review_speech), title, due_at, status, note`

系统：

- `provider_config`：`id, kind(llm|speech|asr), name, base_url, model, secret_ref, enabled`（`secret_ref` 指向本地 keystore，不存明文）
- `app_setting`：键值对配置

迁移：M0 用 `SQLModel.metadata.create_all`，M2 起引入 Alembic。

---

## 6. 关键流程设计

### 6.1 实时语音模拟面试（核心链路）

```text
前端                    后端 /ws/sessions/{id}                实时语音厂商        DeepSeek
 │  start(配置: 岗位/简历/题库/节奏画像)
 │──────────────────────────►│ 建 session、拼系统提示词、选首批题目
 │                           │──── 建立上游 WS（provider adapter）────────►│
 │  audio_append(PCM16 分片) ►│ 转发上游音频 + 本地 VAD 打点              │
 │                           │◄─── asr_partial / asr_final ───────────────│
 │◄── asr_partial (实时字幕) ─│
 │                           │ 用户停顿 > wait_ms → 组装上下文，调 LLM ───┼──► 生成下一个问题/追问
 │                           │◄────────────────────────────────────────────┼── 问题文本
 │                           │ 把问题文本交给语音侧合成（或走 realtime 直出音频）
 │◄── audio_chunk + agent_text│
 │  (可选) interrupt          │ 打断：清空播放队列 + 通知上游 cancel
 │  finish ─────────────────►│ 落库 turns/audio → 异步评分 → 返回 report_id
```

要点：

1. **回合控制器**：回答结束不是靠固定静音，而是 `VAD 静音阈值`（默认 900ms，受画像调节）+ `最短回答时长` + `是否覆盖关键词` 三者共同判定。
2. **打断（barge-in）**：用户开口即暂停面试官音频，前端清播放队列、后端通知上游取消当前响应。
3. **双模态**：`mode=text` 时同一条链路跳过音频，直接 WS 文本事件，用于安静环境/快速刷题。
4. **容错**：上游断线→重连并回放最近 2 轮上下文；连不上→提示可切 Mock/文字模式。
5. **音频留存**：整场录音 + 每回合切片都落 `data/audio/{session_id}/`，供复盘回听与二次精修 ASR。

### 6.1.1 阿里云百炼接入细节（已核实）

线上协议是 **OpenAI Realtime 方言**，因此 `providers/speech/` 只需一个 `RealtimeWSProvider` 基类，可同时接百炼与任何 OpenAI-Realtime 兼容端点。

- 端点：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<model>`（新加坡：`dashscope-intl`）；新业务空间专属域名 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime`
- 鉴权：`Authorization: Bearer $DASHSCOPE_API_KEY`（可带 `OpenAI-Beta: realtime=v1`）。**Key 有地域绑定**，北京 Key 不能连新加坡端点，设置页要做地域下拉 + 连接自测
- 音频格式：输入 `pcm16` 16kHz 单声道；输出 `pcm24` 24kHz（注意前端播放队列要按 24k 解码，或做一次重采样）
- 建连后**必须先发一次 `session.update`** 再发音频；单会话最长 **120 分钟**，超时自动断开 → `RealtimeWSProvider` 内建「到 110 分钟主动重连 + 上下文续接」

**两种语音模式（同一套对前端的统一事件协议，靠配置切换）**

| 模式 | 链路 | 优点 | 代价 |
|------|------|------|------|
| `cascade`（**默认**） | 实时 ASR(`qwen3-asr-flash-realtime`) → **DeepSeek 出题/追问** → TTS(`qwen3-tts-flash`) 播放 | 题目、追问、节奏 100% 由 DeepSeek + 题库控制；ASR 文本天然入库，评分不用二次转写；成本可精算 | 延迟比端到端高（ASR 断句 → LLM 首 token → TTS 首包） |
| `end_to_end` | `qwen-omni-*-realtime` 直接语音进出，人设/题库/节奏策略写进 `instructions` | 延迟最低、语气最自然 | 出题控制力弱，DeepSeek 只能做会后评分；历史音频每轮重复计入输入 token，长会话变贵 |

事件映射（我们 ↔ 线上）：

| 我方事件 | 线上事件 |
|---|---|
| `asr_partial` | `conversation.item.input_audio_transcription.delta` |
| `asr_final` | `conversation.item.input_audio_transcription.completed` |
| `turn_state(speech_started/stopped)` | `input_audio_buffer.speech_started` / `speech_stopped` |
| `agent_text_delta` | `response.audio_transcript.delta`（e2e）／DeepSeek 流式 token（cascade） |
| `audio_chunk` | `response.audio.delta`（base64，pcm24） |
| `turn_end` | `response.done` |
| 上行 `audio_append` | `input_audio_buffer.append` |
| 上行 `text` / 注入题目 | `conversation.item.create` + `response.create` |
| 上行 `interrupt` | `response.cancel` + `input_audio_buffer.clear` |

其他要点：VAD 交给服务端 `turn_detection.server_vad`（`silence_duration_ms` 由节奏画像的 `wait_ms` 动态调），打断默认开启；`semantic_vad`、联网搜索仅 Qwen3.5 系列支持，先不用；音频 token 换算按所选模型核对（如 turbo 系列 每秒音频 ≈ 25 token），会话结束写入 `cost_estimate`。

### 6.2 「学习面试官节奏」怎么做

**输入**：用户上传的真实面试录音（+ 可选标注）。**输出**：`interviewer_profile.params`。

抽取流水线（`analysis/pacing_extract.py`）：

1. **转写**：上传 → 批量 ASR（说话人分离）→ 带时间戳的说话人段落。
2. **切分问题**：按说话人轮次 + 问句特征（疑问词、句尾语气、是否承接上文）切成「问题单元」。
3. **打标**：每题用 LLM 分类（类型 / 主题 / 追问链），并抽取关键词与「面试官关注的考点」。
4. **量化特征**：
   - `interviewer_wpm` 语速、`avg_question_len`、`followup_depth`（平均追问层数）
   - `wait_ms`（问完到下一位说话的间隔中位数）、`interrupt_rate`（是否频繁打断）
   - `speaking_ratio`（面试官:候选人 发言时长比）
   - `topic_distribution`（各题型占比）、`stress_level`（追问压迫感，由措辞与打断率推导）
   - `opening/closing_pattern`（开场自我介绍要求、结尾反问与薪资话术模板）
5. **写库**：`interviewer_profile.params` 示例

```json
{
  "pace": {"interviewer_wpm": 220, "candidate_wpm_target": 180, "wait_ms": 1200},
  "followup": {"max_depth": 3, "trigger": ["回答<20s", "缺少量化", "未覆盖关键词"]},
  "mix": {"self_intro": 0.08, "project_deep": 0.45, "behavior": 0.2, "tech": 0.22, "reverse": 0.05},
  "stress": "high",
  "style_tags": ["紧追技术细节", "要求数据支撑", "打断频繁"]
}
```

6. **模拟时如何用**：`PacingEngine` 把 params 转成运行时策略——等待阈值、追问触发条件、题目混合比例、TTS 语速与停顿、是否允许打断。→ 这正是「按学到的节奏进行面试」。

### 6.3 评分、纠错与教学

`analysis/rubric.py` 定义 7 个维度，每项 1–5 分，且**必须给出证据回合 id**：

| 维度 | 看什么 |
|------|--------|
| 切题度 | 是否正面回答被问的问题，有无跑题 |
| 结构 | STAR / 总分总 / 结论先行 |
| 信息密度与量化 | 是否有数字、指标、可验证结果 |
| 技术深度与正确性 | 概念是否准确，边界与取舍是否讲到 |
| 表达与流畅 | 语速、口头禅、长时间停顿、录音音频特征 |
| 时间控制 | 单题回答时长是否落在合理区间（受节奏画像约束） |
| 主动性 | 反问质量、反问时机、薪资沟通 |

输出 `score_report` + `improvement_item[]`，每条包含：
`problem`（一句话指出问题）→ `evidence_turn_ids`（引原话）→ `suggestion`（怎么改）→ `corrected_answer`（用用户自己的经历重写的示范答案，不是模板话术）→ `drill`（可立刻做的练习）。

LLM 分工：`deepseek-reasoner` 做批改与重写（质量优先），`deepseek-chat` 做实时对话（延迟优先）。

### 6.4 薄弱点与针对性训练

- 每个 `improvement_item` 与题目标签映射到 `skill_topic`，更新 `skill_mastery`：
  `new = clamp(old + k * (score_norm - old))`，`k=0.3`；连续两次低分标记为「高危薄弱点」。
- 抽题器（`learning/selector.py`）权重：`w = α·薄弱度 + β·岗位JD匹配度 + γ·SM-2到期 + δ·题型覆盖平衡`。
- 首页给「今日训练」：3 道薄弱点题 + 1 道随机，做完更新掌握度；报表给掌握度曲线与高危清单。

### 6.5 上传自己的面试录音做复盘

入口：拖拽上传 → 选「这是真实面试 / 模拟练习」→ 异步任务（进度可查）→ 报告页。
流程：批量 ASR（说话人分离）→ 逐题切分 → 对每个候选人回答跑 §6.3 评分 → 额外产出「面试官问题清单 + 你的应答质量地图」→ 与历史面试对比（同公司/同题型趋势）。

### 6.6 投递数据统计与平台同步

**现实约束**：Boss 直聘 / 猎聘 / 智联 / 51job / 拉勾均无公开开放 API，直接「同步」只有这几条路：

| 方式 | 可行性 | 说明 |
|------|--------|------|
| 手动录入 + CSV/Excel 导入 | 高（**首发，M6 必做**） | 字段映射 + 去重（company+title+applied_at 哈希） |
| 浏览器扩展抓取自己账号的投递记录页 | 中高（**M6 做**） | 用户已登录态下读取自己页面 → POST 到本地 `/api/applications/ingest`；需处理 DOM 变更、遵守平台条款 |
| **半自动投递助手（本项目自建，§6.7）** | 高（**M10**） | 自己在已登录浏览器里抓岗位 + 生成招呼语 + 确认后投递；既是投递动作也是数据来源 |
| IMAP 解析通知邮件 | 中（本次不做） | 覆盖「投递成功/面试邀请/感谢信」，能自动推进状态；已在 `sync/` 预留 adapter 位 |
| 平台开放平台 / 合作方 API | 低 | 基本拿不到 |

统计产出：按渠道的 投递量 / 简历通过率 / 面试转化率 / 各阶段转化漏斗、按周趋势、简历版本 A/B（同简历版本 vs 回复率）、响应时长分布、公司维度的面试进度看板。

### 6.7 半自动投递助手（融合 get_jobs 的能力内化）

**定位**：`loks666/get_jobs` 是成熟的「自动投递执行器」，本项目吸收它的**功能设计与流程**，用 Python 重实现为**半自动**投递助手；**不引入 Java 运行时，不复制其源码与数据文件**（License 见下）。

**为什么是 Python 内化，而不是改用 Java 复用其代码（已核实）**：get_jobs 用 `com.microsoft.playwright:playwright:1.62.0` + npm 的 `patchright-core@1.62.1`（装配成 driver，靠 `-Dplaywright.cli.dir` + `PLAYWRIGHT_NODEJS_PATH` 让 Java 客户端去驱动 patchright 的 Node driver）——即 **Java 根本没有官方 Patchright，反检测是手工装配出来的**。Python 有官方 `patchright` 包，`import` 替换即用，反检测起点**不低于** get_jobs；而真正需要自己写的部分（各平台 DOM 选择器与投递动作）**与语言无关**。所以语言不构成能力差异，只影响"能否直接抄它的源码"——在 License 禁止商业化、且我们走的是"半自动 + 人工确认"新流程的前提下，复用价值有限。若将来平台适配成本实在压不住，退路是让它做旁路投递器、我们读它的 SQLite（§12 第 9 条），而**不是**把主项目改写成 Java。

**从 get_jobs 借鉴的 7 个设计（明确对应）**：

| get_jobs 的做法 | 我们的落地 |
|---|---|
| 平台抽象层（每平台一个类：`Boss.java`/`Liepin.java`…） | `apply/adapters/base.py` 定义统一接口 `search_posts() / fetch_detail() / send_greeting() / send_resume_image()`，先实现 `boss.py`，`liepin.py` 第二 |
| 智能过滤规则链（不活跃 HR、猎头、薪资不符） | `apply/filters.py` 规则链，规则持久化在 `apply_rule`，UI 可开关、调参、看命中统计 |
| AI 匹配 + 按 JD 生成打招呼语 | 复用 `analysis/resume_match` 打分排序；`apply/say_hi.py` 用 DeepSeek 结合**用户真实经历 + 岗位 JD**生成，禁止群发式空话 |
| 图片简历自动发送（Boss） | 浏览器端 pdf.js 把简历转 JPG（不出本机）→ 投递后按平台能力附加（仅 Boss，默认关） |
| 黑名单自动更新（识别"不合适"回复） | 从会话/通知识别拒绝信号 → 写 `blacklist`，不再重复投；同时支持手动拉黑 |
| 超长 Cookie 持久登录 | `platform_account.login_state_path`（Playwright `storage_state`，落在 `data/browser/`，加密 + 本机权限） |
| 企业微信实时通知 | 默认改为**本地桌面/Web 通知**；企业微信 webhook 作为可选通道 |

> 城市/行业/薪资/经验/学历枚举我们自建精简版，**不直接搬它的代码表数据文件**（License 与体积双重考虑）。

**执行流（人工在环，第 ⑤ 步是闸门）**：

```text
① 搜索抓取   按 target（关键词/城市/薪资/经验）打开搜索页 → 抓卡片+详情 → job_posting
② 过滤链     apply_rule 逐条筛：库里已投过 / 黑名单 / 薪资不符 / 猎头 / 不活跃 HR / 外包关键词 …
③ 匹配打分   resume_match 打分 + 排序 → 生成「今日候选」列表（含打分理由）
④ 草拟       对候选生成打招呼语（+ 可选图片简历）→ apply_task(status=pending)
⑤ ★人工确认  逐条「通过 / 改文案 / 跳过」，或批量通过已高分项（默认禁止全自动跳过闸门）
⑥ 投递       runner 按限速策略逐条执行；失败重试 ≤2 次；每条都记 application(source=apply_bot) + application_event
⑦ 熔断       一旦检测到验证码 / 风控提示 / 登录失效 → circuit_broken_until=当天结束，立刻停止并通知
```

**风控与合规（写死在默认值里，不依赖自觉）**：

- 默认**每日 ≤30 条、每分钟 ≤3 条**，随机抖动 3–9s；上调需二次确认弹窗。
- 默认**只投「你在 UI 上确认过」的岗位**，不做无人值守批量投递。
- 检测到登录失效 / 验证码 / 频率限制 → **当天熔断停投**（这正是 get_jobs 作者反复强调的"掉线当天别投"）。
- 模块**默认关闭**，需在设置页显式开启并勾选「我了解平台条款与账号风险」。
- **不实现**：验证码绕过、多账号轮换、代理 IP 池、指纹伪造 → 这类能力会让封号与合规风险陡增。
- 反检测用 `patchright` + 人类化节奏（随机延迟、逐字输入）；**不做**指纹农场式方案。
- DOM 改版导致适配失效时，必须**显式报错到 UI**（绝不静默错投），并把它当作「随时会坏、坏了不影响其他功能」的模块。

**License 说明**：get_jobs 采用 **PolyForm Noncommercial 1.0.0**（免费非商业使用，**禁止任何商业使用**）。本项目只借鉴公开的功能设计与流程，**不复制其源码与数据文件**，且定位为个人自用、非商业。

### 6.8 Offer 决策与薪资谈判

- **Offer 对比矩阵**：`offer` + `offer_score`，维度含总包/月数/奖金/股权/福利/通勤/成长/稳定性/城市/落户/税后实发，权重可拖；输出加权得分 + **敏感性分析**（权重微调后排名是否翻转 → 暴露"你其实最在意什么"）。
- **薪资谈判模拟**：复用 §6.1 的实时语音链路，做一个 `role=hr_negotiator` 的画像（`style_tags: ["压价","竞品施压","拖延","内部平衡"]`），战术可切换；结束后的评分维度换成：**锚点设定 / 信息不对称处理 / 让步节奏 / 底线守住率 / 话术清晰度**。
- **谈判简报自动生成**：目标区间（基于 `application` 里同岗位薪资分布 + 你的年限/技能）、底线值、可让步项（签字费/职级/入职时间/股票）、话术脚本；谈判结果回填 `offer` 形成记录。

### 6.9 避坑与条款提醒的合规边界（纪律，必须执行）

- **公司研究与避坑**（虚假招聘 / 培训贷 / 外包）：只做「**聚合来源 + 原文摘录 + 明确标注未经验证**」。**禁止输出结论性判断**（"这家公司在裁员""这是骗子公司"）——此处 LLM 幻觉会直接害人。`prep_pack.sources` 强制非空，每条信息都要能点开原始链接。
- **合同 / 竞业条款审查**：只做**风险条款清单提醒**（试用期时长与工资比例、竞业范围与补偿、违约金、社保公积金基数、发薪日、调岗与考核条款），页面固定声明"**非法律意见，重要条款请咨询专业人士**"。
- 这两条写进 M12 验收标准，作为可测试项（例：断言输出里不含结论性断言、`sources` 非空）。

---

## 7. 接口设计

REST（`/api`）：

- `targets` / `resumes` / `questions`：标准 CRUD + 批量导入
- `POST /sessions` → `{session_id, ws_url, mode}`；`POST /sessions/{id}/finish`；`GET /sessions/{id}`（含 turns）；`GET /sessions/{id}/report`
- `POST /recordings`（multipart）→ `{recording_id, task_id}`；`GET /recordings/{id}/analysis`
- `GET|POST /interviewer-profiles`；`POST /interviewer-profiles/{id}/apply`
- `GET /skills/mastery`；`GET /training/next`
- `POST /applications/import`；`GET /applications/stats?by=channel|week|company`
- **投递助手**：`POST /apply/accounts/{platform}/login`（打开浏览器扫码，落 storage_state）；`POST /apply/search`（按 target 抓岗位）；`GET /apply/candidates`；`GET|PUT /apply/rules`；`POST /apply/tasks/{id}/approve|skip`；`POST /apply/tasks/approve-batch`；`POST /apply/run`；`GET /apply/status`（含 `circuit_broken_until`）；`GET /apply/history`
- **分析与决策**：`GET /analytics/funnel?by=channel|week`；`GET /analytics/rejection-attribution`；`GET /analytics/jd-keywords`；`GET|POST /offers`；`PUT /offers/{id}/score`；`POST /negotiation/sessions`
- **人脉与准备**：`GET|POST /contacts`；`POST /contacts/{id}/events`；`GET /contacts/due-followups`；`POST /prep-packs`（按 application 生成）；`GET|POST /exports?kind=markdown|excel|pdf`
- `GET|PUT /settings/providers`；`POST /settings/test-connection`

WebSocket `/ws/sessions/{id}`（M1 已实现；事件名以 `app/realtime/session_runtime.py` 为准）：

- 上行：`audio_append(base64 pcm16@16k)` `text` `interrupt` `control(end_turn)` `finish` `ping`
  （不需要 `start`：会话由 `POST /api/sessions` 创建，开场题已落库，WS 只负责音频与事件）
- 下行：`state`（含 `audio_mode` / `speech_provider` / `text_fallback`）`interviewer_text` `candidate_turn`
  `asr_partial` `asr_final` `speech_state` `barge_in` `audio_chunk` `metrics(audio_ms, speaking, threshold)`
  `turn_meta` `awaiting_text` `finished` `error` `pong`

所有事件带 `seq`，便于断线续接与前端去重。

录音回放：`GET /api/sessions/{id}/audio`（整场 WAV）、`GET /api/sessions/{id}/audio/turns/{idx}`（单回合切片）。

---

## 8. 前端页面

1. **面试间（核心）**：左侧对话流（面试官问题 + 你的字幕，可切纯文字输入）、中间波形与计时、右侧节奏指示（当前画像的语速/追问深度/下一题类型预测）、底部「打断 / 跳过 / 结束并评分」。
2. **报告页**：总分 + 7 维雷达图 + 逐题评分卡（原话证据 / 问题 / 建议 / 示范答案）+ 音频回听（跳转到对应回合）。
3. **上传复盘页**：拖拽上传、进度、报告。
4. **题库页**：增删改、标签、参考要点、来源筛选（手动/JD 生成/录音抽取/面经导入）。
5. **简历与话术页**：简历多版本对比、JD 匹配与 ATS 体检报告、自我介绍生成器、STAR 故事卡、标准回答库、敏感题答案。
6. **投递执行台（§6.7）**：今日候选（匹配分 + 打分理由 + 过滤命中项）→ 勾选/改招呼语/通过 → 一键投递（限速进度条）；**熔断与异常状态置顶告警**；过滤规则可视化开关；平台账号登录与每日额度。
7. **投递分析页**：漏斗图、渠道对比、周趋势、简历版本 A/B、拒信归因、JD 关键词热度、待跟进清单、CSV/扩展导入器。
8. **人脉页**：联系人列表、沟通时间线、到期跟进提醒、节日问候与跟进话术草案。
9. **Offer 决策页**：对比矩阵（拖权重 + 敏感性分析）、谈判简报与谈判模拟入口、入职清单。
10. **薄弱点/训练页**：掌握度清单、高危薄弱点、今日训练入口、学习路径与完成度。
11. **设置页**：语音/LLM Key 与连接自测、投递模块总开关与风险确认、限速与额度、成本统计、数据导出（Markdown/Excel/PDF）与清除。

---

## 9. 里程碑计划

每个里程碑都以「可演示 + 有测试」为完成标准。M0–M2 是主干，M3 起是可独立迭代的价值点。

> **进度**：✅ **M0 已完成**、✅ **M1 已完成**（代码在 `backend/` 与 `frontend/`，56 个 pytest 通过，文字与语音两条链路均已端到端冒烟验证；细节见 `README.md` 的进度表）。M2 进行中。

| 里程碑 | 内容 | 验收标准 |
|--------|------|----------|
| **M0 骨架与文字闭环**（1–2 天） | repo 初始化、后端 FastAPI + SQLite + SQLModel、前端 Vite/React/Tailwind、DeepSeek provider、MockRealtime、最小会话流（文字问答 + 结束） | 浏览器里能用文字完成一场面试并落库；`pytest` 覆盖会话生命周期 |
| **M1 实时语音链路**（3–5 天） | AudioWorklet 采集（pcm16@16k）、WS 双向音频、百炼 `RealtimeWSProvider`（cascade：ASR→DeepSeek→TTS）+ Mock、服务端 VAD 与回合控制、打断（barge-in）、110 分钟重连、录音存档与回放 | 对着麦克风完成一场 ≥10 分钟语音面试：可打断、延迟体感可用、断线可恢复；录音与逐轮回放可听；无 Key 时 Mock 模式跑通同一条链路 |
| **M2 题库 + 会话管理**（2–3 天） | 题库 CRUD、按岗位 JD 生成题、会话列表/详情、回合精修转写、Alembic | 题库可维护；历史面试可回看回听 |
| **M3 评分与教学质量**（3–4 天） | Rubric 打分、证据引原话、纠错建议、示范答案重写、报告页与雷达图 | 每场结束 30s 内出报告；评分逻辑有单元测试与若干人工校验样本 |
| **M4 节奏学习**（3–4 天） | 上传真实录音、说话人分离、问题切分与打标、节奏特征抽取、画像管理、画像在模拟中生效 | 上传 2 段真实录音能生成画像，且模拟时可观察等待/追问/题型比例随之变化 |
| **M5 薄弱点与训练**（2–3 天) | 标签树、掌握度模型、SM-2 复习、加权抽题、今日训练、掌握度曲线 | 低分题在下次出题中明显被优先命中；掌握度随练习变化可解释 |
| **M6 投递数据**（3–4 天） | `job_posting` / `application` 分表 + `apply_task` / `platform_account` / `blacklist` 建表（§9.1 第 6–7 条）；CSV 导入去重 + 漏斗/渠道统计；浏览器扩展（MV3）抓取已登录账号的投递页 → 本地 ingest 接口；状态流转与待跟进 | 导入/抓取历史投递后能按渠道出漏斗与转化率；扩展在目标站点可一键同步且失败有明确提示 |
| **M7 打磨**（持续） | 报告导出 PDF、Key 本地加密、成本统计、暗色主题、错误兜底与空态 | — |

**建议的第一刀**：M0 里就要把 `providers/*/base.py` 抽象和 `session_turn` 落库做对，后面所有功能都吃这两个地基。

### 9.1 主干必须提前预留的设计（防止 M8+ 返工）

以下 7 条是「现在多花 10 分钟、后面省一次重构」的清单，M0–M6 期间必须做到：

1. **音频与转写全部带时间戳落盘**（M1）：`session_turn.start_ms/end_ms` + `data/audio/{sid}/turn_{idx}.wav`。没有这个，M9 表现力分析（卡顿定位到句子）要重做一遍。
2. **所有 AI 分析写成 `analysis/` 下的纯函数 + JSON schema 输出**：M8 的简历诊断、M10 的准备包与拒信归因都直接复用这套调用与校验，不再各写一套 prompt。
3. **`score_report.dimension_scores` 用 JSON 存**（M3）：M9 新增「表达体检」维度、M11 新增「抗压」维度、M12 新增「谈判」维度都只是加 key，不改表结构。
4. **`resume` / `job_target` / `application` 建表时就带上文本与版本字段**（M0/M2/M6）：`resume.content_text` + `variant_label`、`job_target.jd_text`、`application.jd_text` + `resume_version`。M8 的匹配诊断、M10 的关键词热度与拒信归因全靠它。
5. **LLM 调用统一带 `task` 标签**（M0）：`task=interview_turn|scoring|resume_match|say_hi|prep_pack|...`，用于按任务换模型、算成本、做效果对比。
6. **岗位与投递分成两张表**（M6）：`job_posting`（抓到的岗位）与 `application`（我投的）必须分开，并用 `source` + `external_id` 记录来源。M10 的半自动投递、去重、拒信归因、JD 关键词热度全依赖这个分离——现在合成一张表，将来一定要拆。
7. **投递执行状态机先留字段**（M6）：`apply_task`、`platform_account.daily_sent/circuit_broken_until`、`blacklist` 即便 M6 不用也先建出来，M10 才能只加逻辑不改数据。

### 9.2 M8+ 扩展路线图（已确认纳入，主干完成后开工）

排序按「性价比 × 依赖就绪度」——A/B/D 组只吃已有数据（简历 / 录音 / 投递表），C 组与硬核模块工程量大、放最后；**M10 是最重的一块**（既产数据、又发投递动作）。

| 里程碑 | 内容 | 依赖 | 验收 |
|--------|------|------|------|
| **M8 简历与话术智能**（3–4 天） | `analysis/resume_match.py` 简历×JD 匹配诊断（匹配度 + 缺失关键词 + 逐条改写建议）；**ATS 关键词优化与格式体检**（关键词密度/格式/可读性）；简历多版本管理与标注；自我介绍生成器（30s/1min/3min + 计时练）；`story_card` STAR 故事库 + 标准回答库（模拟时按题型自动调用）；敏感题个性化答案（离职/空窗/规划/薪资）；反问清单 | M0/M2 的 `resume`·`job_target` | 上传简历+JD 出匹配与 ATS 报告；故事卡能在模拟中被自动引用；敏感题答案用**你自己的经历**生成而非模板 |
| **M9 表现力分析**（2–3 天） | 表达体检：填充词词频（中文填充词表）、WPM 曲线、停顿分布并**定位到具体句子**、音量/语速突变推断紧张度 + 对应练习；可选视频仪态（浏览器端 MediaPipe：眼神/低头/表情） | M1 录音 + M3 转写 | 每场结束出表达体检；每条问题能跳转到音频对应位置 |
| **M10 半自动投递 + 求职 CRM**（5–7 天） | `apply/`：Boss 适配层 + 过滤链（`apply_rule`）+ 匹配排序 + AI 招呼语 + 图片简历 + 限速器 + 熔断（§6.7）；投递执行台 UI（人工确认闸门）；浏览器扩展抓投递页；投递看板与漏斗、简历版本 A/B；**AI 面试准备包**（含来源）；面试日程 + 提醒 + `.ics`；感谢信/跟进话术起草；**拒信归因**；JD 关键词热度与技能雷达 | M8 打分 + M6 投递表 | 能用确认式流程完成一次真实投递并正确落库；模拟异常时熔断生效；漏斗能按渠道出转化率；归因页能指出最该补的短板 |
| **M11 压力模式与多角色**（3–4 天） | 追问到底（`max_depth` 5 层）、打断模式、限时抢答、**面试官风格库预设**（压力/技术/HR/行为/群面）；问题预测与追问树（简历+JD+面经生成）；多角色连环面（技术→交叉→主管→HR，多画像 + 多音色）；英文面试（双语 rubric） | M4 节奏画像 | 三种压力模式可独立开关；一场会话内能切换 ≥2 个面试官角色且音色可区分 |
| **M12 Offer 与决策**（3–4 天） | Offer 对比矩阵 + 敏感性分析；薪资谈判模拟（HR 角色 + 战术切换）与谈判简报；公司研究避坑（**只给来源 + 未经验证标注**）；合同/竞业条款提醒（**非法律意见**） | M3 + M10 | 矩阵能随权重变化翻转排名；谈判模拟给出底线守住率；避坑条目全部可点开原始来源（写成断言测试） |
| **M13 硬核技术面 · P3**（5–8 天） | 算法题白板（Monaco + 浏览器端判题沙箱）、系统设计白板（Excalidraw + 设计题评分维度）、群面/无领导小组多 agent 混音 | M2 题库 + M11 | 能给出"思路—取舍—边界"的评估，不只看对错 |
| **M14 人脉与学习计划**（2–3 天） | 联系人 CRM（内推/校友/HR：沟通时间线、节日问候、到期跟进提醒）；技能差距 → 学习路径与完成度追踪；第二平台投递适配（猎聘） | M10 | 到期联系人不再漏跟进；学习路径与薄弱点联动 |
| **M15 入职与长期 + 导出集成**（3–4 天） | 入职清单（背调/体检/材料/离职交接/租房/落户）、试用期 OKR、转正述职、长期规划（晋升/跳槽时机/薪资基准）；导出 Markdown/Excel/PDF、Notion/飞书、本地开放 API | M6 + M12 | 一键导出全量数据与报告；入职清单可勾选追踪 |

**两个技术规避点（别踩）**：

- **视频仪态用浏览器端**（`@mediapipe/tasks-vision`，npm 包），不要用 Python 端 MediaPipe——本机 Python 3.14 很可能没有对应 wheel，且视频不该往服务端传。
- **算法题判题用浏览器端沙箱**（Pyodide / JS 执行 + 超时中断），不要在本机跑用户/LLM 生成的代码——本机没有 docker，起不了隔离容器。


---

## 10. 验证策略

- 纯逻辑（评分聚合、节奏特征、掌握度更新、抽题权重、漏斗统计）→ pytest 单元测试，先用固定样本断行为。
- 提供方 → `providers/*/mock.py`，CI/无 Key 环境下全流程可跑。
- 前端 → Vitest 测 WS 事件归约；Playwright（可选）跑一次端到端文字面试。
- 每里程碑跑一次「真实 5 分钟语音面试」人工验收，记录延迟与错字率。

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| Python 3.14 新版本生态 | 依赖装不上 | 全部走云 API，不用本地 ML 库；如遇轮子缺失，回退用 3.12/3.13 虚拟环境（`.venv`，README 写清） |
| 实时语音厂商协议差异大 | 换供应商成本高 | adapter + 统一事件协议（§7），先接 1 家 + Mock，第二个供应商证明抽象成立 |
| 中文 ASR 对人名/技术名词识别差 | 评分基于错文本 | 术语表（自定义热词）+ 面试后二次精修转写 + 报告里允许手动纠正回合文本后再重评 |
| 延迟高像对讲机 | 体验差 | 上行 20–40ms 分片、服务端流式转发、`deepseek-chat` 而非 reasoner 做实时轮、前端预缓冲播放 |
| 面试录音隐私 | 数据泄露 | 全程本机存储；上传第三方 ASR 前提示；提供「仅本地留存/不送云端」开关与一键清除 |
| 招聘平台数据获取 | 拿不到数据或有合规风险 | 不做爬虫式批量抓取；只用「自己账号 + 手动/CSV 导入 + 扩展读自己页面」；遵守平台条款，抓取频率与字段最小化 |
| 百炼 Key 地域绑定 / 会话上限 | 连不上或长面试被切断 | 设置页显式选地域 + 连接自测；`RealtimeWSProvider` 到 110 分钟主动重连并续接上下文；音频 token 随轮次累积，超长会话提醒收尾 |
| API 费用失控 | 长期使用贵 | 每场会话记录 token 与音频分钟数 + 成本估算；提供「只用文字/只用便宜模型」模式 |
| LLM 评分不一致 | 结果不可信 | 固定 rubric + JSON schema 输出 + 低温度 + 抽样人工校验；保留历史分数做漂移监控 |
| get_jobs 为 PolyForm Noncommercial | 不能商用、不能搬源码 | 只借鉴其功能设计与流程，**不复制源码与数据文件**；本项目定位个人自用、非商业（§6.7） |
| 自动化投递导致封号 | 账号受限，求职中断 | 模块**默认关闭** + 半自动人工确认 + 每日 ≤30 / 每分钟 ≤3 + 异常当天熔断；**不实现**验证码绕过、多账号轮换、代理 IP 池 |
| 招聘平台 DOM 改版 | 投递适配随时可能失效 | 适配层隔离、失败**显式报错到 UI**（绝不静默错投）、启动自检；把它当作"坏了不影响其他功能"的模块 |
| 公司研究/避坑的 LLM 幻觉 | 误导用户做错决策（比不做事更糟） | 只给来源 + 原文摘录 + "未经验证"标注，**禁止结论性判断**；`sources` 强制非空并写成测试用例 |
| 条款提醒被当成法律意见 | 法律风险 | 只做条款清单 + 固定免责声明，不做定性结论 |
| patchright 跟随上游版本升级 | 升级可能引入行为变化 | 锁版本 + 升级后跑一次真实投递冒烟测试；用官方 Python 包避免手工装配 driver 的版本错配（get_jobs 就踩过 `patchright-core` 1.51.3 的 `locator.count()` bug） |

---

## 12. 已确定决策

| # | 决策 | 结论 | 影响 |
|---|------|------|------|
| 1 | 产品形态 | 本地优先 Web 应用（本机 FastAPI + 浏览器前端，数据落本地） | 录音用 `getUserMedia`；无需账号体系与云部署 |
| 2 | 技术栈 | Python (FastAPI) + React/TS (Vite) | 见 §3；Python 3.14 下不用本地 ML 库 |
| 3 | 语音方案 | 云端实时语音 API + DeepSeek 做大脑 → 阿里云百炼，默认 `cascade`（实时 ASR → DeepSeek → TTS），保留 `end_to_end` 模式 | M1 先接百炼 + Mock；详见 §6.1.1 |
| 4 | 投递数据来源 | CSV/Excel 导入 + 浏览器扩展抓取自己账号的投递页；**不做** IMAP 邮件解析（预留 adapter） | M6 范围见 §6.6 |
| 5 | 扩展范围 | A 简历与话术智能 / B 表现力分析 / C 压力与硬核面 / D 求职流程闭环 **全部纳入**，排为 M8–M15 | 见 §9.2；**主干 M0–M7 优先**，但需遵守 §9.1 的预留约束 |
| 6 | get_jobs 融合方式 | **能力内化**：Python 侧自建 `apply/` 半自动投递模块，借鉴其平台抽象层 / 过滤规则链 / 配置 schema / 黑名单 / 持久登录设计；**不引入 Java 运行时、不复制其源码**（PolyForm Noncommercial） | 见 §6.7；新增依赖 `patchright` |
| 7 | 自动化程度 | **半自动**：AI 筛选 + 草拟招呼语 → 用户逐条确认 → 限速投递；模块默认关闭，检测到风控当天熔断 | 见 §6.7 风控清单 |
| 8 | 新增功能边界 | 纳入 26 条清单中除「匿名社区/面经共享」外的全部（社区需云后端与多用户，与本地优先冲突）；公司研究/合同审查按 §6.9 降级为「来源 + 提醒」 | 见 §6.9 |
| 9 | 技术路线 | **维持全 Python**（FastAPI + patchright 官方 Python 包 + DeepSeek/百炼），不改用 Java；退路是「让 get_jobs 做旁路投递器、我们读它的 SQLite」，而非改写主项目 | 依据见 §3 / §6.7：Java 无官方 Patchright，Python 反检测起点不低于它，且 AI/语音/分析生态更强 |

仍需在动手前敲定的小项（有默认值，不阻塞开工）：

1. 是否并行管理「多份简历 × 多个岗位」→ **默认做**（M0 就把 `resume`/`job_target` 建成一对多）。
2. 百炼业务空间地域 → **默认北京**（`dashscope.aliyuncs.com`），你到设置页填 Key 时确认。
3. 面试间默认语言/音色 → **默认中文、Chelsie 类通用音色**，设置页可换。
