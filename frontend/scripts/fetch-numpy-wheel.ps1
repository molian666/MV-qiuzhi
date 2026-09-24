<#
.SYNOPSIS
    手动取 Pyodide 的 numpy wheel，放进 public/pyodide/，让 Python 判题完全离线可用。

.DESCRIPTION
    通常不需要跑这个：`pnpm build` / `pnpm dev` 会自动尝试下载。
    但 node 的 fetch 不走系统代理，本机实测直连会卡很久——这条脚本改用
    Invoke-WebRequest，它会走系统代理，反而更稳。

    下载后校验 sha256（对照 pyodide-lock.json），不匹配就删掉重试而不是留个坏文件。
    文件落在 frontend/public/pyodide/，该目录不进 git。

.EXAMPLE
    pwsh -File scripts/fetch-numpy-wheel.ps1
#>
[CmdletBinding()]
param(
    [string[]]$Mirror = @(
        'https://cdn.jsdelivr.net/pyodide',
        'https://fastly.jsdelivr.net/pyodide'
    )
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Split-Path -Parent $scriptDir
$pyodidePkg = Join-Path $frontendDir 'node_modules\pyodide'
$targetDir = Join-Path $frontendDir 'public\pyodide'

if (-not (Test-Path $pyodidePkg)) {
    throw "找不到 $pyodidePkg，先在 frontend 目录跑 pnpm install"
}

$version = (Get-Content (Join-Path $pyodidePkg 'package.json') -Raw | ConvertFrom-Json).version
$lock = Get-Content (Join-Path $pyodidePkg 'pyodide-lock.json') -Raw | ConvertFrom-Json
$entry = $lock.packages.numpy

if (-not $entry) {
    throw 'pyodide-lock.json 里没有 numpy 条目，Pyodide 版本可能变了'
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$destination = Join-Path $targetDir $entry.file_name

if (Test-Path $destination) {
    $existing = (Get-FileHash $destination -Algorithm SHA256).Hash.ToLower()
    if ($existing -eq $entry.sha256) {
        Write-Host '[跳过] numpy wheel 已在本地且校验通过' -ForegroundColor Green
        Write-Host "       $destination"
        return
    }
    Write-Host '[提示] 本地文件校验不通过，重新下载' -ForegroundColor Yellow
}

foreach ($base in $Mirror) {
    $url = "$base/v$version/full/$($entry.file_name)"
    Write-Host "[下载] $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $destination -TimeoutSec 300 -UseBasicParsing

        $digest = (Get-FileHash $destination -Algorithm SHA256).Hash.ToLower()
        if ($entry.sha256 -and $digest -ne $entry.sha256) {
            $wanted = $entry.sha256.Substring(0, 12)
            $got = $digest.Substring(0, 12)
            Write-Host "[警告] sha256 不匹配（期望 ${wanted}…，实得 ${got}…）" -ForegroundColor Yellow
            Remove-Item $destination -Force
            continue
        }

        $size = [math]::Round((Get-Item $destination).Length / 1MB, 1)
        Write-Host "[完成] numpy wheel ${size} MB，sha256 校验通过" -ForegroundColor Green
        Write-Host "       $destination"
        return
    } catch {
        Write-Host "[重试] 该镜像失败：$($_.Exception.Message)" -ForegroundColor Yellow
    }
}

throw '所有镜像都失败了。检查网络或代理设置，也可以手动下载后放进 frontend/public/pyodide/。'
