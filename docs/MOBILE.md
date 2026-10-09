# 手机端使用与「随时随地面试」

> 状态：**M19 手机能打开 已完成**；M20/M21 的核心机制已落地（见 §5 实施记录），
> 页面逐页顺手化（M22）待做。本文只讨论**手机浏览器作为客户端**这件事，
> 部署本身（自签证书、云服务器、Tailscale）见 [`DEPLOY.md`](DEPLOY.md)。
> 里程碑编号接 README 的 M18，从 **M19** 起。

---

## 1. 结论先行

### 1.1 真正缺的不是"把页面缩小"，而是三件基础设施

| # | 基础设施 | 现状 | 结论 |
|---|----------|------|------|
| 1 | **安全上下文（HTTPS）** | 已有：`deploy.ps1` 生成自签证书 + `use_tls`，`DEPLOY.md` 零节给了 Tailscale 方案 | 选型问题，不是开发问题。**选定 Tailscale**（真证书，手机免装） |
| 2 | **同源 + 认证 + 静态托管** | 已有：`_mount_frontend` 单端口托管 `frontend/dist`；`auth.py` cookie 登录 + WS 握手校验 | 已经满足，不用改 |
| 3 | **会话在手机上"活得下来"** | ❌ 原本完全没有 | **这才是 M20/M21 的全部内容** |

第 3 条是这份方案的价值所在：**手机上最糟的体验不是报错，而是"看起来正常但什么都不动"**。

### 1.2 推荐形态（已确认）

```
手机（Safari / Chrome）
   │  https://<机器名>.<tailnet>.ts.net     ← Tailscale 签的真证书，手机免装
   ▼
tailscale serve（在主力机上终止 TLS，转发明文 HTTP）
   ▼
uvicorn  127.0.0.1:8000  ← **HOST 保持 127.0.0.1**
   └─ 同时托管前端 dist、REST、WebSocket
```

三个关键取舍：

1. **手机零配置**。自签证书在手机上很痛：iOS 要"安装描述文件 + 到『关于本机 → 证书信任设置』里手动开完全信任"两步（第二步极易漏）；Android 的安装入口各家 ROM 都不一样，而且 **Android 上 Chrome 不能靠点警告页放行**——证书装不对就一直没有麦克风。
2. **投递功能从手机也能用**。因为 `HOST=127.0.0.1`，`deploy.is_local_deployment()` 仍返回 `True`，投递执行与扩展抓取不会被 403（`DEPLOY.md` 第 46–49 行已经论证过这一点）。反过来，为了手机改成 `HOST=0.0.0.0` 的局域网方案，会把这三个接口打成 403。
3. **一个反直觉的坑**：走 Tailscale 时 **`COOKIE_SECURE` 必须保持 `false`**。serve 在它对外的 443 上终止 TLS，转发给后端的是明文 HTTP；若 cookie 带 `Secure`，浏览器不会在明文连接上回传它 → 登录页反复跳回。`mobile.ps1` 检测到这种组合会直接警告。

### 1.3 局域网退路

不想装 Tailscale 时：`pwsh -File scripts\mobile.ps1 -Mode lan`。
它调用 `deploy.ps1` 生成含该 IP 的证书，并打印手机装证书的步骤（iOS 两步 / Android 路径差异）。
代价：**每台手机手动信任一次，而且换 IP（DHCP）就要重签重装**。

> 两条路都**不需要给前端加第二个端口**，也**不要**为了手机另起一个 Vite dev server。
> 想先在手机上看界面：`pnpm dev --host` 用 `http://<局域网IP>:5173` 也能看（Vite 会代理 `/api`、`/ws`），
> 但**那条路麦克风一定用不了**——必须 HTTPS。

---

## 2. 手机上到底会坏在哪（原始缺口清单）

| # | 缺口 | 原始证据 | 手机上的后果 |
|---|------|----------|--------------|
| G1 | WebSocket 不重连 | `api/ws.ts` 只有 `connect/close` | 锁屏/切后台/地铁断网 → 会话**永久卡死** |
| G2 | `speechSynthesis` 未解锁 | `audio/player.ts` 在 `setTimeout` 回调里 `speak()` | **iOS：面试官完全不出声**，且不报错 |
| G3 | `AudioContext` 可能在非手势路径创建 | `player.ts` 的 `ensureContext()` 在 `audio_chunk` 到达时调用 | iOS 上 context 变 `suspended` → 服务端 TTS 模式没声音 |
| G4 | 无后台挂起处理 | 全仓无 `visibilitychange` | 切回前台：麦克风轨道已死、context 暂停、WS 已断，但**界面看起来一切正常** |
| G5 | 无屏幕常亮 | 无 `wakeLock` | 面试中 30 秒自动锁屏 → 触发 G4 |
| G6 | 视口用 `h-screen` | `App.tsx`；无 `dvh`/`safe-area` | 地址栏伸缩导致布局跳动；Home 条压住底部按钮 |
| G7 | 打断阈值是固定音量 | `InterviewVoice.tsx`：`BARGE_IN_LEVEL = 0.02` | 手机外放时**面试官把自己打断** |
| G8 | 无手机端可用性视图 | — | 用户分不清"HTTP 访问/证书没信/权限被拒"，三种失败长得一样 |

---

## 3. 实施记录（M19 已完成）

| 产出 | 位置 | 说明 |
|------|------|------|
| 一条命令打通手机访问 | `scripts/mobile.ps1` | 自动选模式（装了 Tailscale 就用它）；**强制要求有登录密码**（没有就生成并写入 `.env`）；打印访问 URL；生成二维码 PNG 并打开 |
| 终端二维码 | `backend/tools/qr.py` | **纯标准库手写**编码器（字节模式 / 纠错 M / 版本 1–10 / 自动选掩码）。不引依赖的理由：ISO/IEC 18004 是定死的规范，不存在"跟着上游变"的风险 |
| 客户端环境体检端点 | `backend/app/api/client_info.py` | `GET /api/client-info`，**免鉴权**（在 `auth.PUBLIC_PATHS` 里）：用户最可能卡在登录页排查麦克风问题，那时还没有 cookie |
| 设备能力探测 | `frontend/src/mobile/useDevice.ts` | 读 `isSecureContext`、`mediaDevices`、`speechSynthesis`、`visualViewport` |
| 屏幕常亮 | `frontend/src/mobile/useWakeLock.ts` | 切后台会被系统释放，回前台自动重申；iOS 低电量模式必然失败，如实提示而不是静默 |
| 就绪体检卡 | `frontend/src/components/PhonePreflight.tsx` | 三件事逐项给绿灯或**可执行的修复指引**；全部正常时只占一行，不挤占手机屏幕 |
| 动态视口与安全区 | `frontend/src/index.css` | `.app-viewport`(100dvh)、`.pb-safe`(safe-area-inset-bottom)、`.touch-target`(44px) |

**M21 的核心机制也已经落地**（下面 §4 有清单），它们不只是"手机专用"——
桌面端切标签页、休眠唤醒同样受益。

---

## 4. M21：语音链路在手机上不中断（已落地）

| 机制 | 位置 | 要点 |
|------|------|------|
| 音频解锁 | `frontend/src/audio/unlock.ts` | 在**用户手势里**创建/恢复 `AudioContext` 并 `speak('')` 预热语音合成。所有"开始面试/开始说话"按钮都先过它 |
| 断线退避重连 | `frontend/src/api/backoff.ts` + `api/ws.ts` | 600ms 起指数退避、封顶 15s、带抖动、上限 8 次；`lastSeq` **不清零**，重连后重复下发的事件被去重挡住 |
| 连接状态如实显示 | `InterviewVoice.tsx` | "已连接 / 重连中… / 已断开"，而不是只显示"已连接"让用户对着不动页面发呆 |
| 可见性恢复 | `InterviewVoice.tsx` | 回到前台依次检查：麦克风是否还在出数据（用"最近一次分片时刻"判断，比读轨道状态可靠）→ `AudioContext` → WS → 重申常亮锁，并把结果**显示出来** |
| 自适应打断阈值 | `frontend/src/audio/bargein.ts` | 底噪取 30% 分位数 → 阈值 = 底噪 × 3.2，夹在 [0.012, 0.09]，触发后 900ms 冷却；**换输入设备时 `reset()`** 重新估 |
| 长录音风险 | — | 手机上建议只留逐回合录音（整场录音的内存占用见 §6 风险表） |

---

## 5. 验收

### 5.1 自动化（已通过）

```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest -q      # 1398 项（含 test_qr.py 36、test_client_info_api.py 8）
cd frontend; pnpm test                                   # 35 项（backoff / bargein / unlock / thinking）
```

钉住的行为（都是"静默坏掉"的类型，只能靠测试守）：

- 二维码**解回来等于原文**（`test_qr.py` 自带一个独立解码器，双向往返），
  以及掩码选择确实取惩罚分最低者、保留区不被数据压坏、渲染是正方形；
- 没有 Key 时 `/api/client-info` 不泄露任何密钥字段；
- 重连退避在异常输入下**不产生 NaN**（`setTimeout(fn, NaN)` 会变成 0ms 忙等循环）；
- 解锁是**幂等**的：重复调用不再新建 `AudioContext`（iOS 上多开会互抢音频焦点）；
- 打断判定在"外放回采"场景下**不触发**、在安静房间不被电流声触发。

### 5.2 真机清单（只能人工，别用"浏览器模拟手机尺寸"替代）

| 项 | iOS Safari | Android Chrome |
|---|---|---|
| 能打开（HTTPS 无警告） | ☐ | ☐ |
| 麦克风权限弹窗 | ☐ | ☐ |
| 面试官声音能听见 | ☐ | ☐ |
| 切后台 30s 再回来能续 | ☐ | ☐ |
| 锁屏 30s 再回来能续 | ☐ | ☐ |
| 面试中不自动锁屏 | ☐（低电量模式会失败，应给提示） | ☐ |
| 键盘不遮输入框 | ☐ | ☐ |
| 外放不自打断 | ☐ | ☐ |
| 蓝牙耳机可用 | ☐ | ☐ |
| Home 条不压住底部按钮 | ☐ | ☐ |

---

## 6. 风险与取舍

| 风险 | 说明 | 应对 |
|------|------|------|
| **iOS 播报是硬伤** | `speechSynthesis` 后台会被暂停、长文本会被截断、锁屏直接停 | 已做手势预热 + 每次回到前台 resume；仍不行就退化为**文字为主、语音为辅**，并明确显示 |
| **手机外放自打断** | 扬声器与麦克风 <1cm，AEC 与语音合成抢焦点时不可靠 | 自适应阈值 + 冷却窗；真机实验后再定默认倍数，必要时提示戴耳机 |
| **长录音内存** | 整场 WAV 在手机浏览器里累积，可能触发页面回收 | 沿用分片落盘策略；手机上默认只留逐回合录音 |
| **证书反复折腾** | 局域网自签 + DHCP 换 IP = 重签重装 | 首选 Tailscale 真证书；`mobile.ps1` 会打印当前 IP 并提示"IP 变了要重跑" |
| **手机就是全部数据入口** | 手机丢了等于别人能开简历库，走 Tailscale 还等于能触发投递 | `mobile.ps1` **默认强制生成强密码**（不接受留空）；cookie 有效期已有显示 |
| **两套部署认知负担** | 多了"手机模式"这个概念 | 不新增 `HOST` 之外的开关；手机上不可用的功能**在页面里说清原因**，而不是藏起来 |

---

## 7. 还没做的（M22/M23）

1. **M22 页面逐个顺手化**，按使用频率排序：面试间（M19/M20 已做）→ 报告 → 历史面试 → 训练 → 表达体检。
   投递执行台、题库批量管理、导出、系统配置**明确不做**——它们在手机上本来就别扭，
   保留"请到电脑上操作"的诚实提示即可。
2. **M23 手机当第二屏**（可选）：主力机开摄像头看简历、手机当话筒。技术上只是多端同源 + 会话状态在服务端，有明确需求再做。

---

## 8. 复现一次「手机可用」

```powershell
# 1) 装 Tailscale（一次）：winget install Tailscale.Tailscale，然后 tailscale up
# 2) 让服务进入手机可用状态（会顺手补 AUTH_PASSWORD、开 serve、打印二维码）
pwsh -File scripts\mobile.ps1
# 3) 启动后端（另开一个窗口，保持开着）
cd backend; .\.venv\Scripts\python.exe -m app
# 4) 手机扫二维码（或在同一个 tailnet 的任意设备上打开那个 https 地址），用密码登录
```

关掉这套转发：`tailscale serve --https=443 off`。
