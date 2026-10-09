# 部署到 Windows 云服务器

把 mianshi 跑在一台 Windows 云服务器上，用**单端口 + 自签证书 + 密码登录**访问。

---

## 零、先判断：真的需要公网部署吗

**大多数情况不需要。** 如果这个系统只有你一个人用、投递和录音都在你自己的电脑上做，
那么更省事的形态是「服务只跑在主力机上，远程访问走私有组网」：

| | 公网部署（下文一～八节） | 主力机 + Tailscale（本节） |
|---|---|---|
| 投递执行 | **不可用**：机房 IP 命中平台风控，也没有能扫码登录的桌面 | 可用 |
| 麦克风（语音面试/录音） | 要装自签证书 + 手动信任，**手机上尤其难受** | **免配置**（真证书） |
| 数据位置 | 云上（简历、录音、面试记录、模型 Key） | 只在你的机器上 |
| 谁能访问 | 全网任何人都在扫 | 只有你自己的设备 |
| 月成本与维护 | 服务器月费 + 安全更新 + 证书轮换 | 0（Tailscale 个人版免费） |
| 第二个人要用 | 得自己做多用户 | 不支持 |

一句话：**公网部署砍掉的是这个项目最有价值的功能（把简历真投出去），
换来的是「主力机关机时也能打开」。** 这个交换值不值，取决于你的主力机多久不在线。

> **手机端请看 [`MOBILE.md`](MOBILE.md)**：本节讲的是"怎么让手机能访问"，
> 那一份讲的是"访问之后为什么手机上还是会坏"——iOS 的音频解锁、断线重连、
> 切后台恢复、自适应打断阈值，以及一条命令打通链路的 `scripts/mobile.ps1`。

### 怎么做

```powershell
# 主力机：绑回 loopback，投递与录音保持全功能
HOST=127.0.0.1
PORT=8000
# 仍然建议设密码：手机丢了别人就能打开。配上密码后所有设备都要登录，
# 靠 cookie 记住，本机也只登一次，日常无感。
AUTH_PASSWORD=<强密码>

# 装 Tailscale（个人版免费）。先在管理后台确认已开启 MagicDNS 与 HTTPS Certificates，
# 然后（serve 的语法在 1.52 改过，报错就先跑 tailscale serve --help 确认）：
tailscale serve --bg --https=443 http://localhost:8000
```

得到 `https://<机器名>.<tailnet>.ts.net`，证书由 Tailscale 自动签发并续期
（Let's Encrypt，`*.ts.net`）——浏览器直接认可，于是**麦克风在所有设备上
都免配置**。这一点是自签证书方案做不到的，也是本节真正的价值所在。

`serve` 与 `funnel` 的区别很关键：**`serve` 只有 tailnet 成员能访问，
`funnel` 才是对公网公开**。这里要用 `serve`。

### 服务认为自己在本机，所以投递照常

因为 `HOST=127.0.0.1`，`require_local_run` 会放行，**投递在主力机上可用，从手机触发也可以**。
「本机干活、别处看」这个形态不需要两套部署，也不需要同步——数据只有一份。

### 云服务器还剩什么用途

- **备份**：定期把 `backend/data/app.db` 传上去（对象存储比云服务器便宜一个数量级）。
  **不要为了「用上服务器」去设计主备双向同步**——同步引入的问题通常比它解决的多。
- **应急入口**：只有「主力机长期关机、而你又随时要看」时才需要。真到那一步，
  按下文一～八节部署即可，代码是现成的。

---

## 一、先说清三个前提（都是产品形态层面的变化）

项目原本的设计是「本地优先：本机 FastAPI + 浏览器，数据落本地，无需账号体系」。
上公网会打破这个前提，所以这三件事必须做，**不是可选项**：

| 变化 | 为什么 |
|------|--------|
| **必须设登录密码** | 服务里有你的简历、面试录音、逐轮问答、投递记录，还配着 DeepSeek Key。没密码等于把这些挂在公网上。代码里有硬闸：`HOST` 不是本机且 `AUTH_PASSWORD` 为空时**拒绝启动**。 |
| **必须用 HTTPS（或 localhost）** | 浏览器只在安全上下文里允许 `getUserMedia`。HTTP 上麦克风直接被拒，语音面试与录音上传全废。 |
| **投递执行与扩展抓取不在云端** | 平台按 IP + 设备指纹风控，云服务器 IP 投递基本等于立刻触发验证码；而且它们依赖你本机的浏览器登录态。云端这三个接口（`/api/apply/search`、`/api/apply/execute`、`/api/applications/ingest`）会返回 403 并说明原因，其余功能全部可用。 |

---

## 二、前置条件

云服务器上需要：

- **Python 3.11+**（本项目在 3.14.6 上测过）
- **Node 20+ 与 pnpm**（只在服务器上构建前端时需要；也可以本地 build 后把 `frontend/dist` 传上去）
- **git**（两个用途：拉代码；`usr/bin/openssl.exe` 用来生成证书）
- 服务器安全组**放行你选定的端口**（默认 8443）

---

## 三、一键部署

以管理员身份打开 **PowerShell 7（`pwsh`）**，在仓库根目录：

```powershell
pwsh -File scripts\deploy.ps1 -HostAddress 47.98.1.2 -Port 8443 -RegisterTask
```

`-HostAddress` 填**你实际用来访问的地址**（公网 IP 或域名）。它必须和证书的 SAN 一致，否则浏览器判定不匹配。

脚本做五件事：

1. 检查虚拟环境、Python 版本、管理员权限
2. 生成自签证书到 `backend\certs\`（PEM 格式，uvicorn 直接可用）
3. 往 `backend\.env` 写入 `HOST/PORT/AUTH_PASSWORD/COOKIE_SECURE/SSL_*`，并**打印一次随机生成的强密码**
4. 开放 Windows 防火墙端口
5. 打印启动命令；加了 `-RegisterTask` 就注册为开机自启的计划任务

> **请把打印出来的密码记下来**（它也在 `backend\.env` 里）。`.env` 已被 gitignore，别提交。

不加 `-RegisterTask` 则只做准备，前台的启动方式是：

```powershell
cd backend
.\.venv\Scripts\python.exe -m app
```

`python -m app` 会读 `.env` 里的 host/port/证书，所以改端口或换证书不用动服务定义。

---

## 四、让浏览器不再警告（这一步不做，麦克风用不了）

自签证书浏览器不认，首次访问会警告。把证书导入系统信任一次，之后就没有警告了：

1. 把服务器上的 `backend\certs\server.crt` 下载到你的电脑
2. **Windows**：`certmgr.msc` → 受信任的根证书颁发机构 → 证书 → 右键导入 → 选 `server.crt`
   **macOS**：钥匙串访问 → 系统 → 拖入 `server.crt` → 双击 → 展开「信任」→ 改为「始终信任」
3. **完全重启浏览器**（只刷新页面不够，Chrome 会缓存证书状态）

然后访问 `https://47.98.1.2:8443`，用脚本给的密码登录。

> 证书 825 天后过期（这是浏览器接受的最长有效期），重新跑 `deploy.ps1` 前先删掉 `backend\certs\` 两个文件。

---

## 五、环境变量一览

部署相关的都在 `backend\.env`（完整模板见 `backend\.env.example`）：

| 变量 | 说明 |
|------|------|
| `HOST` | 云上填 `0.0.0.0`。**非本机地址 + 空 `AUTH_PASSWORD` = 拒绝启动** |
| `PORT` | 监听端口，默认 8443（避开 443，非管理员也能绑） |
| `AUTH_PASSWORD` | 登录密码。为空则整个应用退化成「只能本机访问」模式 |
| `AUTH_SECRET` | 会话签名密钥。**留空会自动在 `data\.auth_secret` 生成并持久化**，重启不会把所有设备踢下线 |
| `COOKIE_SECURE` | HTTPS 部署填 `true`（会话 cookie 只经 TLS 发送） |
| `SSL_CERTFILE` / `SSL_KEYFILE` | PEM 证书与私钥路径。两个都配才启用 TLS |
| `STATIC_DIR` | 前端构建产物目录。留空则自动用 `frontend/dist`（存在就由后端托管） |
| `TRUST_PROXY_HEADERS` | 自己上 Nginx/Caddy 反代时填 `true` |
| `EXPORT_TOKEN` | 导出接口的令牌。**不设则导出功能一律 401**，与登录是两道独立的门 |

---

## 六、上安全清单（逐条打勾）

- [ ] `AUTH_PASSWORD` 是随机生成的强密码，不是 `123456`
- [ ] `backend\.env` 没进 git（`git status --ignored` 里能看到它）
- [ ] `backend\certs\` 没进 git（同上；私钥泄露比密码泄露更麻烦）
- [ ] `EXPORT_TOKEN` 也设了（导出是全量数据，值得单独一把锁）
- [ ] 云服务商安全组只放行了你的端口，且**限制来源 IP**（能限就限，这是最省事的一层）
- [ ] `COOKIE_SECURE=true`（HTTPS 部署时）
- [ ] 确认云端拿不到投递能力（访问 `/api/apply/queue` 能看，`/api/apply/execute` 返回 403）

---

## 七、更新部署

```powershell
cd <仓库目录>
git pull
cd backend
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"      # 依赖有变时
cd ..\frontend
pnpm install && pnpm build                                  # 前端有变时，产出 dist
Stop-ScheduledTask -TaskName mianshi
Start-ScheduledTask -TaskName mianshi
```

数据库是 SQLite，表结构变更靠 `create_all`——**已存在的表不会加新列**。这是已知约束（要改列得先引入 Alembic，属于 M2 的工作）。新增功能的字段一律走「加 JSON 字段」而不是「加列」，所以现在升级不需要迁移。

---

## 八、故障排查

**启动就报「拒绝启动：HOST=... 会让本机之外的人访问，但 AUTH_PASSWORD 为空」**
这是安全闸，不是 bug。要么在 `.env` 里设 `AUTH_PASSWORD`，要么把 `HOST` 改回 `127.0.0.1`。

**登录后每次刷新都要重登**
`AUTH_SECRET` 没设、而且 `data\.auth_secret` 写不进去（目录权限）。检查 `backend\data` 是否可写。

**浏览器能打开但语音面试提示麦克风被拒**
证书没导入信任（见第四节），或者没重启浏览器。地址栏如果是「不安全」就说明还在用未信任的证书。

**`https://IP:8443` 打不开**
依次查：服务是否在跑（`Get-ScheduledTaskInfo -TaskName mianshi`）、防火墙规则（`Get-NetFirewallRule -DisplayName 'mianshi (8443)'`）、云服务商安全组。

**导出接口全返回 401**
`EXPORT_TOKEN` 没设。设上并重启，然后在「入职与长期 → 数据导出」里填同一个令牌。

**想只在本机用**
`HOST=127.0.0.1` + 空 `AUTH_PASSWORD` 就行，行为与本轮改动之前完全一致（`uvicorn app.main:app --reload`）——防护不该让本地开发变麻烦。

---

## 九、这套东西不做什么

- **不做多用户**：只有一个密码、一个会话概念。本来就只有你一个人用。
- **不做找回密码**：忘了就改 `.env` 重启。
- **不做 token 刷新**：30 天过期后重新登录。
- **不在服务端跑用户或模型生成的代码**：算法题判题（M13）会放在浏览器端沙箱里，理由见 `docs/PLAN.md` 的两个技术规避点——公网服务器上跑这类代码比本地更危险。
