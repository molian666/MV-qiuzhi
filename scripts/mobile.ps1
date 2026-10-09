<#
.SYNOPSIS
    让手机能打开并正常使用 mianshi（登录 + 语音面试 + 麦克风）。

.DESCRIPTION
    做四件事：
      1. 检查前置条件（虚拟环境、前端构建产物）；
      2. 确保 .env 里设了 AUTH_PASSWORD——服务一旦能被手机访问，**必须有密码**；
      3. 按模式打通访问链路：
           tailscale（默认，推荐）：用 tailscale serve 暴露本机的 HTTP 端口，
             由 Tailscale 签发**真证书**（Let's Encrypt），手机免装证书即可用麦克风；
           lan：局域网 + 自签证书，不用装 Tailscale，但**每台手机都要手动信任证书**；
      4. 打印手机该访问的 URL 与终端二维码（扫码比手敲域名可靠）。

    为什么要 HTTPS：浏览器只在安全上下文（HTTPS 或 localhost）里给麦克风。
    手机上访问 http://<局域网IP> 属于"不安全上下文"，getUserMedia 直接失败。

.PARAMETER Mode
    auto（默认，装了 Tailscale 就用它）/ tailscale / lan。

.PARAMETER HostAddress
    lan 模式用哪个局域网 IP 签证书。默认自动挑一个局域网 IPv4。

.PARAMETER Port
    lan 模式下自签证书要签的端口（只是记录，实际监听端口仍取 .env 的 PORT）。

.PARAMETER NoQr
    不打印二维码。

.EXAMPLE
    pwsh -File scripts\mobile.ps1
    pwsh -File scripts\mobile.ps1 -Mode lan -HostAddress 192.168.1.23

.NOTES
    **服务本体始终只监听 127.0.0.1**（见 .env 的 HOST）：
    tailscale serve 在本机做转发，局域网方案由 uvicorn 直接监听。
    保持 HOST=127.0.0.1 还有个额外好处——后端认为"我在本机运行"，
    于是投递执行与扩展抓取**从手机也能用**（见 backend\app\deploy.py）。
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', 'tailscale', 'lan')]
    [string]$Mode = 'auto',

    [string]$HostAddress = '',

    [int]$Port = 8443,

    [switch]$NoQr
)

$ErrorActionPreference = 'Stop'
# 本文件必须存成「UTF-8 with BOM」：PowerShell 5.1 对无 BOM 的 UTF-8 会按 ANSI(GBK)
# 解析，中文注释与字符串会变乱码、甚至导致语法错误（deploy.ps1 / dev.ps1 都带 BOM）。

# 这里**刻意不动** [Console]::OutputEncoding。
# 试过把它设成 UTF-8 并配 PYTHONIOENCODING=utf-8，结果是 Python 发出的 UTF-8 字节
# 被仍然按 cp936 渲染的控制台解读成乱码——在 .NET 里"编码对了"不等于控制台真的按它解码
# （本机实测：chcp 报 936，[Console]::OutputEncoding 报 utf-8，两者并不一致）。
# 所以：PowerShell 自己输出中文（走控制台 API，正常），跨进程的 Python 输出只用 ASCII。
# 二维码改走 PNG 文件，彻底绕开终端编码。

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$frontendDist = Join-Path $repoRoot 'frontend\dist'
$envFile = Join-Path $backendDir '.env'
$qrTool = Join-Path $backendDir 'tools\qr.py'

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK]   $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "  [警告] $Text" -ForegroundColor Yellow }
function Write-Info([string]$Text) { Write-Host "         $Text" -ForegroundColor Gray }

# --------------------------------------------------------------------------- #
Write-Step '1/5 检查前置条件'
# --------------------------------------------------------------------------- #

$python = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "找不到虚拟环境：$python`n请先在 backend 目录执行：python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e '.[dev]'"
}
Write-Ok "Python：$python"

if (-not (Test-Path $envFile)) {
    throw "找不到 $envFile`n请先复制模板：Copy-Item backend\.env.example backend\.env"
}

if (-not (Test-Path $frontendDist)) {
    Write-Warn2 '还没构建前端（frontend\dist 不存在），手机打开会是 404。'
    Write-Info '先执行：cd frontend; pnpm build'
} else {
    Write-Ok "前端构建产物：$frontendDist"
}

# 从 .env 读配置（只读键值对，不执行任何东西）
#
# 两条规则都是**踩坑之后定下来的**：
#
# 1. **必须显式用 UTF-8 读**。Windows PowerShell 5.1 的 Get-Content / Select-String
#    默认按 ANSI(cp936) 解码，遇到 .env 里的中文注释会整段错位，键就读不到了。
# 2. **同名键取最后一个**，与 dotenv 的"后定义覆盖先定义"一致。
#    `.env.example` 给的是 `AUTH_PASSWORD=` 这样的空模板行，用户的值往往追加在后面——
#    取第一个匹配会永远读到空值（实测踩过：连续两次重复生成密码、往 .env 里堆重复键）。
function Get-EnvValue([string]$Key) {
    $raw = [System.IO.File]::ReadAllText($envFile, [System.Text.UTF8Encoding]::new($false))
    $matches = [regex]::Matches($raw, "(?m)^$Key=(.*)$")
    if ($matches.Count -eq 0) { return '' }

    # 取"最后一个非空值"；全为空则返回空字符串
    $last = ''
    foreach ($match in $matches) {
        $value = $match.Groups[1].Value.Trim()
        if ($value) { $last = $value }
    }
    return $last
}

# 写回 .env：**替换掉一行空的同名键**（.env.example 里的模板行），而不是再追加一行。
# 返回 $true 表示替换成功，$false 表示文件里没有可替换的空行（调用方改为追加）。
function Set-EnvEmptyKey([string]$Key, [string]$Value) {
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $raw = [System.IO.File]::ReadAllText($envFile, $encoding)
    # `\r?` 不能省：.env 在 Windows 上是 CRLF，而 .NET 多行模式下 `$` 是**在 \n 之前**匹配，
    # 于是行尾那个 `\r` 会挡住 `[ \t]*$` 这种"行尾必须是空白"的写法，匹配数直接是 0。
    # （这条也是实测出来的：脚本当时悄悄退化成"追加一行"，在 .env 里堆重复键。）
    $pattern = "(?m)^$Key=[ \t]*\r?$"
    if (-not [regex]::IsMatch($raw, $pattern)) { return $false }

    # 只替换第一处空行，其余重复行原样保留（不悄悄删用户的配置）
    $script:envKeyReplaced = $false
    $result = [regex]::Replace($raw, $pattern, {
            param($match)
            if ($script:envKeyReplaced) { return $match.Value }
            $script:envKeyReplaced = $true
            return "$Key=$Value"
        })
    [System.IO.File]::WriteAllText($envFile, $result, $encoding)
    return $true
}

$envHost = Get-EnvValue 'HOST'
$envPort = Get-EnvValue 'PORT'
if (-not $envPort) { $envPort = '8000' }
if (-not $envHost) { $envHost = '127.0.0.1' }
Write-Ok "后端监听：$envHost`:$envPort"

if ($envHost -ne '127.0.0.1' -and $envHost -ne 'localhost') {
    Write-Warn2 "HOST=$envHost 不是本机地址。"
    Write-Info '这样后端会认为"我在云上"，投递执行与扩展抓取会被 403（见 backend\app\deploy.py）。'
    Write-Info '手机方案不需要改 HOST——Tailscale 走本机转发，局域网由 uvicorn 自己监听即可。'
}

# --------------------------------------------------------------------------- #
Write-Step '2/5 选定访问方式'
# --------------------------------------------------------------------------- #

function Find-Tailscale {
    $onPath = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Tailscale\tailscale.exe'),
        (Join-Path $env:LOCALAPPDATA 'Tailscale\tailscale.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Get-LanAddress {
    # 挑一个私有网段的 IPv4。排除 Tailscale 的 100.64/10 与回环/APIPA：
    # 手机要走的是同一个 Wi-Fi 下的真实局域网地址。
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike '127.*' -and
            $_.IPAddress -notlike '169.254.*' -and
            $_.IPAddress -notlike '100.*'
        } | Sort-Object -Property SkipAsSource, InterfaceMetric
    if ($addresses) { return $addresses[0].IPAddress }
    return $null
}

# 先把模式定下来、该报的错先报掉，**再**去动 .env。
# 反过来的话，用户会因为"Tailscale 没装"看到一次失败，却发现 .env 已经被改过了。
$tailscale = Find-Tailscale
$resolvedMode = $Mode
if ($Mode -eq 'auto') {
    if ($tailscale) { $resolvedMode = 'tailscale' } else { $resolvedMode = 'lan' }
}
if ($resolvedMode -eq 'tailscale' -and -not $tailscale) {
    throw "没找到 tailscale.exe。两种办法：`n  1) 装它（推荐，手机免装证书）：winget install Tailscale.Tailscale`n  2) 改用局域网方案：pwsh -File scripts\mobile.ps1 -Mode lan"
}

if ($resolvedMode -eq 'tailscale') {
    Write-Ok "访问方式：tailscale（真证书，手机零配置）"
} else {
    if (-not $HostAddress) { $HostAddress = Get-LanAddress }
    if (-not $HostAddress) {
        throw '没找到局域网 IPv4。请用 -HostAddress 指定，例如 -HostAddress 192.168.1.23'
    }
    Write-Ok "访问方式：lan（自签证书，手机需手动信任）-> $HostAddress"
}

# --------------------------------------------------------------------------- #
Write-Step '3/5 确保有登录密码'
# --------------------------------------------------------------------------- #
# 只绑 127.0.0.1 时不要密码是合理的（本机自用）。但**手机能访问就等于暴露在网络上**：
# 简历、录音、逐轮问答、模型 Key 都在里面，所以这一步不接受"空着也能跑"。

$password = Get-EnvValue 'AUTH_PASSWORD'
if ($password) {
    Write-Ok 'AUTH_PASSWORD 已设置（沿用现有的，不会覆盖）'
    if ((Get-EnvValue 'AUTH_SECRET') -eq '') {
        Write-Info 'AUTH_SECRET 留空：首次启动会自动生成到 backend\data\.auth_secret，重启不会踢人下线。'
    }
} else {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $password = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')

    # 优先**就地填掉 .env 里那行空的 AUTH_PASSWORD=**（模板行），没有才追加。
    # 只追加的做法会在每次运行后堆一行重复键，后面读的人根本分不清哪个生效。
    if (Set-EnvEmptyKey 'AUTH_PASSWORD' $password) {
        Write-Ok '已写入 backend\.env 的 AUTH_PASSWORD（填掉了原来的空模板行）'
    } else {
        # 追加的内容**保持纯 ASCII**：.env 里混进中文注释会让 PowerShell 5.1 按 ANSI
        # 读错后面的行（见 Get-EnvValue 的注释）。文件本身仍是 UTF-8 无 BOM。
        $block = "`n# ---------- mobile access (written by scripts/mobile.ps1) ----------`nAUTH_PASSWORD=$password`n"
        [System.IO.File]::AppendAllText($envFile, $block, [System.Text.UTF8Encoding]::new($false))
        Write-Ok '已追加 AUTH_PASSWORD 到 backend\.env'
    }

    Write-Host ''
    Write-Host '  +---------------- 访问密码 ----------------+' -ForegroundColor Yellow
    Write-Host "    $password" -ForegroundColor Yellow
    Write-Host '  +------------------------------------------+' -ForegroundColor Yellow
    Write-Info '改完 .env 要**重启后端**才生效。'
}

# --------------------------------------------------------------------------- #
Write-Step '4/5 打开手机访问入口'
# --------------------------------------------------------------------------- #

function Show-Qr([string]$Text) {
    if ($NoQr -or -not (Test-Path $qrTool)) { return }

    # **生成 PNG 并交给系统图片查看器**，而不是在终端里打印方块字符。
    # 终端打印看着聪明，实际很脆：Windows 上"控制台代码页 / Python stdout 编码 /
    # 父进程管道编码"三者经常不一致（本机实测分别看到 936 与 65001），
    # 方块字符一错位就变成 `锟斤拷`——用户扫一个乱码图案只会更困惑。
    # PNG 走图片查看器没有任何编码问题，还能直接印到纸上。
    $pngPath = Join-Path $env:TEMP 'mianshi-mobile-qr.png'
    & $python $qrTool $Text --png $pngPath --no-render | Out-Null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $pngPath)) {
        Write-Info "二维码图片已生成并打开：$pngPath"
        try {
            Invoke-Item $pngPath
        } catch {
            Write-Warn2 "没能自动打开图片，请手动打开上面这个文件。"
        }
    } else {
        Write-Warn2 '二维码生成失败，请手动把上面的地址输入手机。'
    }

    # 终端里再画一份兜底：控制台编码没问题时可以直接扫，出问题也不影响 PNG
    & $python $qrTool $Text --no-render
}

function Show-StartCommand {
    Write-Host ''
    Write-Info "启动后端（保持这个窗口开着）："
    Write-Host "    cd `"$backendDir`"; .\.venv\Scripts\python.exe -m app" -ForegroundColor White
    Write-Info '健康检查：http://127.0.0.1:8000/api/health'
}

# 模式在第 2 步已经定好（$resolvedMode / $tailscale / $HostAddress），这里只管执行。

if ($resolvedMode -eq 'tailscale') {
    # ---------------- Tailscale：真证书，手机零配置 ----------------
    Write-Ok "Tailscale：$tailscale"

    $statusJson = $null
    try {
        $statusJson = & $tailscale status --json 2>$null | ConvertFrom-Json
    } catch {
        $statusJson = $null
    }
    if (-not $statusJson) {
        throw "tailscale 不可用（未登录或服务没起来）。先执行：tailscale up"
    }
    Write-Ok "已登录：$($statusJson.Self.DNSName)"

    # 只代理本机的 HTTP 端口。**不要**给 uvicorn 配 TLS：
    # serve 在它对外的 443 上终止 TLS，转给后端的是明文 HTTP。
    # 因此 COOKIE_SECURE 必须保持 false —— 置 true 会让 cookie 不下发、登录反复跳回登录页。
    Write-Info "配置 serve（首次会弹网页让你为 tailnet 开启 HTTPS 证书，按提示点一下）："
    & $tailscale serve --bg --yes --https=443 "localhost:$envPort"
    if ($LASTEXITCODE -ne 0) {
        throw "tailscale serve 失败（退出码 $LASTEXITCODE）。可先手动跑一次：tailscale serve --bg --https=443 localhost:$envPort"
    }

    $serveStatus = ''
    try { $serveStatus = (& $tailscale serve status 2>$null | Out-String) } catch { }
    $url = $null
    if ($serveStatus -match 'https://([A-Za-z0-9._-]+)') {
        $url = "https://$($Matches[1])"
    } elseif ($statusJson.Self.DNSName) {
        $url = "https://$($statusJson.Self.DNSName.TrimEnd('.'))"
    }
    if (-not $url) {
        Write-Warn2 '没能从 serve status 里读出地址，请手动执行 tailscale serve status 查看。'
    }

    if ((Get-EnvValue 'COOKIE_SECURE') -eq 'true') {
        Write-Warn2 'COOKIE_SECURE=true 与 Tailscale 方案冲突：serve 转发给后端的是明文 HTTP，'
        Write-Info '  浏览器不会在有 Secure 标记的明文连接上回传 cookie，登录会一直失败。'
        Write-Info '  请把 backend\.env 里的 COOKIE_SECURE 改成 false 并重启后端。'
    }

    Write-Host ''
    if ($url) {
        Write-Host "  手机访问地址：$url" -ForegroundColor Green
        Write-Host '  （和其他设备在同一个 tailnet 即可，不需要同一个 Wi-Fi）' -ForegroundColor Gray
        Show-Qr $url
    }
    Show-StartCommand
    Write-Host ''
    Write-Info '关闭这套转发：tailscale serve --https=443 off'
    Write-Info '手机上如果打不开：确认手机装了 Tailscale 且已登录同一账号。'
} else {
    # ---------------- 局域网 + 自签证书（$HostAddress 已在第 2 步定好） ----------------
    $crt = Join-Path $backendDir 'certs\server.crt'
    & (Join-Path $PSScriptRoot 'deploy.ps1') -HostAddress $HostAddress -Port $Port
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "deploy.ps1 退出码 $LASTEXITCODE，请检查上面的输出。"
    }

    $url = "https://${HostAddress}:$Port"
    Write-Host ''
    Write-Host "  手机访问地址：$url" -ForegroundColor Green
    Write-Host '  （手机要连同一个 Wi-Fi；证书必须先信任，否则麦克风用不了）' -ForegroundColor Gray
    Show-Qr $url

    Write-Host ''
    Write-Warn2 '自签证书的代价：每台手机都要手动信任一次，而且**换 IP 就要重签重装**。'
    if (Test-Path $crt) {
        Write-Info "把证书文件传到手机：$crt"
    }
    Write-Host '  iPhone：用 Safari 打开 https://<地址>:<端口> 会提示不安全 -> 继续访问 ->' -ForegroundColor Gray
    Write-Host '          下载描述文件；再到 设置 > 通用 > VPN与设备管理 安装，' -ForegroundColor Gray
    Write-Host '          最后到 设置 > 通用 > 关于本机 > 证书信任设置 里**手动打开完全信任**。' -ForegroundColor Gray
    Write-Host '  Android：设置 > 安全 > 加密与凭据 > 安装证书 > CA 证书，选下载好的 .crt。' -ForegroundColor Gray
    Write-Host '          （各家 ROM 的入口名称不一样；Android 上 Chrome 不能靠点警告页放行。）' -ForegroundColor Gray
    Show-StartCommand
}

# --------------------------------------------------------------------------- #
Write-Step '5/5 现在后端在跑吗'
# --------------------------------------------------------------------------- #
# Get-NetTCPConnection 在某些系统上需要管理员权限，读不到就只说"没确认到"，
# 不能因此让整个脚本失败——前面该做的都已经做完了。
$listening = $null
try {
    $listening = Get-NetTCPConnection -State Listen -LocalPort ([int]$envPort) -ErrorAction Stop
} catch {
    $listening = $null
}
if ($listening) {
    Write-Ok "端口 $envPort 已在监听——如果它是在你改密码之前启动的，记得重启后端。"
} else {
    Write-Warn2 "没有确认到端口 $envPort 上有服务在监听（可能未启动，也可能查询需要管理员权限）。"
    Write-Info '按上面的命令启动后端，再刷新手机上的页面。'
}
Write-Host ''
