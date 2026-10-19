<#
.SYNOPSIS
    在 Windows 云服务器上部署 mianshi（单端口 + 自签证书 + 密码登录）。

.DESCRIPTION
    做四件事：
      1. 生成自签证书（PEM），uvicorn 直接可用；
      2. 检查 backend\.env 里的部署项（AUTH_PASSWORD / TLS 路径 / cookie_secure）；
      3. 开放 Windows 防火墙端口；
      4. 打印启动命令，或用 -RegisterTask 注册为开机自启的计划任务。

    关于自签证书的代价：浏览器首次访问会警告「不安全」，要把证书导入
    「受信任的根证书颁发机构」并信任一次，之后麦克风才能用——浏览器只在
    安全上下文（HTTPS 或 localhost）里允许 getUserMedia。

.PARAMETER HostAddress
    服务器对外 IP 或域名。证书的 SAN 必须包含它，否则浏览器不认。

.PARAMETER Port
    监听端口，默认 8443。

.PARAMETER RegisterTask
    注册为计划任务（开机自启、崩溃后自动重启）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -HostAddress 47.98.1.2 -Port 18443 -RegisterTask

.NOTES
    需要以管理员身份运行（防火墙规则与计划任务都需要）。
    兼容 Windows PowerShell 5.1（云服务器默认自带，无需安装 pwsh）。
    本文件请保持 UTF-8 with BOM 保存，否则 5.1 会按 GBK 解析中文。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$HostAddress,

    [int]$Port = 18443,

    [switch]$RegisterTask,

    [string]$TaskName = 'mianshi'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$envFile = Join-Path $backendDir '.env'
$certDir = Join-Path $backendDir 'certs'
$certPath = Join-Path $certDir 'server.crt'
$keyPath = Join-Path $certDir 'server.key'

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK]   $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "  [警告] $Text" -ForegroundColor Yellow }
function Write-Info([string]$Text) { Write-Host "         $Text" -ForegroundColor Gray }

# --------------------------------------------------------------------------- #
Write-Step '1/5 检查运行环境'
# --------------------------------------------------------------------------- #
if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Ok "Windows PowerShell $($PSVersionTable.PSVersion.Major).$($PSVersionTable.PSVersion.Minor)（脚本已兼容，可直接运行）"
} else {
    Write-Ok "PowerShell $($PSVersionTable.PSVersion)"
}

$python = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "找不到虚拟环境：$python`n请先在 backend 目录执行：python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e '.[dev]'"
}
Write-Ok "Python：$python"
Write-Info (& $python --version)

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) { Write-Ok '以管理员身份运行' } else { Write-Warn2 '不是管理员：防火墙与计划任务会失败' }

# 端口占用预检：别等证书、.env 都弄完了才发现端口被占
$occupied = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($occupied) {
    $procNames = $occupied.OwningProcess | Sort-Object -Unique | ForEach-Object {
        try { (Get-Process -Id $_ -ErrorAction Stop).ProcessName } catch { '?' }
    }
    throw "端口 $Port 已被占用（进程：$($procNames -join ', ')）。换一个端口重跑，例如：-Port 18444"
}
Write-Ok "端口 $Port 空闲"

# --------------------------------------------------------------------------- #
Write-Step '2/5 生成自签证书'
# --------------------------------------------------------------------------- #
# 用 openssl 而不是 New-SelfSignedCertificate：后者产出 PFX，而 uvicorn 只吃 PEM，
# 转换又得回到 openssl。git for Windows 自带 openssl，通常已经在机器上。
#
# 从 git 的安装位置反推，而不是硬编码 %ProgramFiles%：git 可能装在别的盘
# （本机就是 D:\Git），硬编码会直接找不到。
$candidates = @()
$gitExe = (Get-Command git -ErrorAction SilentlyContinue).Source
if ($gitExe) {
    $gitRoot = Split-Path -Parent (Split-Path -Parent $gitExe)
    $candidates += @(
        (Join-Path $gitRoot 'usr\bin\openssl.exe'),
        (Join-Path $gitRoot 'mingw64\bin\openssl.exe')
    )
}
$candidates += @(
    (Join-Path $env:ProgramFiles 'Git\usr\bin\openssl.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Git\usr\bin\openssl.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Git\usr\bin\openssl.exe')
)

$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) {
    $openssl = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $openssl) {
    Write-Warn2 '没找到 openssl（装了 git 就会有）。请手动生成证书后填入 .env，见 docs/DEPLOY.md。'
} else {
    New-Item -ItemType Directory -Force -Path $certDir | Out-Null

    if ((Test-Path $certPath) -and (Test-Path $keyPath)) {
        Write-Ok "证书已存在，跳过生成：$certPath"
        Write-Info '要重新生成就先删掉 certs 目录下的两个文件。'
    } else {
        # SAN 必须包含实际访问用的地址（IP 或域名），否则浏览器判定不匹配。
        # CA:TRUE 是为了让证书能装进「受信任的根证书颁发机构」——只有这样
        # 手动信任一次之后才不会每次访问都警告。
        $san = "IP:$HostAddress"
        if ($HostAddress -match '[a-zA-Z]') { $san = "DNS:$HostAddress" }

        # openssl 会把进度点（"...+..."）写到 stderr，而 $ErrorActionPreference='Stop'
        # 会把它当终止错误、直接中断脚本。临时放宽，并改用**退出码**判断成败
        # （检查文件存在不够：可能留下半成品）。
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $opensslExit = 1
        try {
            & $openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes `
                -keyout $keyPath -out $certPath `
                -subj "/CN=$HostAddress" `
                -addext "subjectAltName=$san" `
                -addext 'basicConstraints=critical,CA:TRUE' `
                -addext 'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign' `
                -addext 'extendedKeyUsage=serverAuth' 2>&1 | Out-Null
            $opensslExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }

        if ($opensslExit -ne 0 -or -not (Test-Path $certPath) -or -not (Test-Path $keyPath)) {
            Remove-Item $certPath, $keyPath -Force -ErrorAction SilentlyContinue
            throw "证书生成失败（openssl 退出码 $opensslExit）。请手动执行脚本里的 openssl 命令，见 docs/DEPLOY.md。"
        }
        Write-Ok "证书：$certPath"
        Write-Ok "私钥：$keyPath"
        Write-Info "有效期 825 天；SAN = $san"
    }
}

# --------------------------------------------------------------------------- #
Write-Step '3/5 写入 backend\.env 的部署项'
# --------------------------------------------------------------------------- #
$existingLines = if (Test-Path $envFile) { Get-Content $envFile -Encoding UTF8 } else { @() }

# 密码：已有就保留（重跑/换端口不该把已登录设备踢下线），没有才生成
$existingPassword = ''
foreach ($line in $existingLines) {
    if ($line -match '^\s*AUTH_PASSWORD\s*=\s*(.+?)\s*$') { $existingPassword = $Matches[1]; break }
}
if ($existingPassword) {
    $password = $existingPassword
    Write-Ok 'AUTH_PASSWORD 已存在，保留原密码'
} else {
    # 用 Create().GetBytes() 而不是静态的 RandomNumberGenerator::Fill——
    # 后者是 .NET 5+ 才有的 API，Windows PowerShell 5.1（.NET Framework）上没有。
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $password = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
}

# 要同步到 .env 的部署项——每次重跑都刷新，换端口直接重跑即可生效
$deployKv = [ordered]@{
    HOST          = '0.0.0.0'
    PORT          = "$Port"
    AUTH_PASSWORD = $password
    COOKIE_SECURE = 'true'
    SSL_CERTFILE  = $certPath.Replace('\', '/')
    SSL_KEYFILE   = $keyPath.Replace('\', '/')
}

# 逐行替换已存在的键，记录哪些键还没落到文件里
$outLines = New-Object System.Collections.Generic.List[string]
$seen = @{}
foreach ($line in $existingLines) {
    $replaced = $false
    foreach ($key in $deployKv.Keys) {
        if ($line -match "^\s*$key\s*=") {
            $outLines.Add("$key=$($deployKv[$key])")
            $seen[$key] = $true
            $replaced = $true
            break
        }
    }
    if (-not $replaced) { $outLines.Add($line) }
}

# 文件里缺失的键，追加到末尾
$missing = @()
foreach ($key in $deployKv.Keys) {
    if (-not $seen.ContainsKey($key)) { $missing += $key }
}
if ($missing.Count -gt 0) {
    $outLines.Add('')
    $outLines.Add('# ---------- 部署（由 scripts\deploy.ps1 写入）----------')
    foreach ($key in $missing) { $outLines.Add("$key=$($deployKv[$key])") }
}

# 显式写无 BOM 的 UTF-8：.env 开头的 BOM 会让首行键名带上不可见字符。
[System.IO.File]::WriteAllText($envFile, ($outLines -join "`r`n") + "`r`n", [System.Text.UTF8Encoding]::new($false))
Write-Ok "已同步 $envFile（HOST=0.0.0.0，PORT=$Port）"

if (-not $existingPassword) {
    Write-Host ''
    Write-Host '  ┌──────────────── 请记下访问密码 ────────────────┐' -ForegroundColor Yellow
    Write-Host "    $password" -ForegroundColor Yellow
    Write-Host '  └────────────────────────────────────────────────┘' -ForegroundColor Yellow
    Write-Info '（也在 .env 里；`.env` 不入库，别提交）'
}

$current = Get-Content $envFile -Encoding UTF8 -Raw
if ($current -notmatch '(?m)^\s*EXPORT_TOKEN\s*=\s*\S') {
    Write-Warn2 'EXPORT_TOKEN 未设置：导出接口会一律返回 401。设置后即可用「入职与长期 → 数据导出」。'
}
if ($current -notmatch '(?m)^\s*CORS_ORIGINS\s*=') {
    Write-Info 'CORS_ORIGINS 未改动——单端口部署下前端与 API 同源，保持默认即可。'
}

# --------------------------------------------------------------------------- #
Write-Step '4/5 开放防火墙端口'
# --------------------------------------------------------------------------- #
$ruleName = "mianshi ($Port)"
if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
    Write-Ok "规则已存在：$ruleName"
} elseif ($isAdmin) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Ok "已开放 TCP $Port"
} else {
    Write-Warn2 "需要管理员权限才能开放端口，请手动执行："
    Write-Info "New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
}

# --------------------------------------------------------------------------- #
Write-Step '5/5 启动方式'
# --------------------------------------------------------------------------- #
$startCommand = "& '$python' -m app"
Write-Info "前台启动（调试用）："
Write-Host "    cd `"$backendDir`"; $startCommand" -ForegroundColor White

if ($RegisterTask) {
    if (-not $isAdmin) { throw '注册计划任务需要管理员权限' }

    $action = New-ScheduledTaskAction -Execute $python -Argument '-m app' -WorkingDirectory $backendDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero)

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -RunLevel Highest -Force -Description 'mianshi 求职操作系统（单用户本地服务）' | Out-Null
    Start-ScheduledTask -TaskName $TaskName

    Write-Ok "已注册计划任务：$TaskName（开机自启，崩溃后自动重试）"
    Write-Info "查看状态：Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
    Write-Info "停止：   Stop-ScheduledTask -TaskName $TaskName"
    Write-Info "卸载：   Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
} else {
    Write-Info '要注册为开机自启的计划任务，加 -RegisterTask 参数重跑。'
}

# --------------------------------------------------------------------------- #
Write-Step '完成，接下来'
# --------------------------------------------------------------------------- #
Write-Host @"
    1. 先构建前端（只需一次；以后改了前端代码再重跑）：
         cd frontend; pnpm install; pnpm build
       后端会自动托管 frontend/dist，一个端口就够，不要在服务器上跑 vite dev。

    2. 到云服务商控制台的「安全组 / 防火墙」放行 TCP $Port。
       Windows 防火墙只挡本机入站，阿里云/腾讯云还有一层安全组，不开手机连不上。

    3. 打开 https://${HostAddress}:${Port}（浏览器会先警告，见下）
    4. 让浏览器的警告消失：把证书导入「受信任的根证书颁发机构」
         - 下载 certs\server.crt 到本机
         - Windows：certmgr.msc → 受信任的根证书颁发机构 → 证书 → 导入
         - macOS：  钥匙串访问 → 系统 → 拖入 → 双击 → 始终信任
       导入后**必须重启浏览器**，麦克风才能在语音面试里用。
    5. 用上面的密码登录；手机浏览器直接访问同一地址即可。
    6. 云上不提供投递执行与扩展抓取（要家庭 IP 与本机登录态），
       /api/apply/search、/api/apply/execute、/api/applications/ingest 会返回 403 并说明原因。
"@ -ForegroundColor Gray
