# 本地开发启动脚本（PowerShell）
# 用法：
#   .\scripts\dev.ps1 backend   # 只起后端（http://127.0.0.1:8000）
#   .\scripts\dev.ps1 frontend  # 只起前端（http://127.0.0.1:5173）
#   .\scripts\dev.ps1           # 两个都起（各开一个窗口）
param(
    [ValidateSet('backend', 'frontend', 'both')]
    [string]$Target = 'both'
)

$root = Split-Path -Parent $PSScriptRoot

function Start-Backend {
    $venvPython = Join-Path $root 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Host "未找到后端虚拟环境，请先执行：" -ForegroundColor Yellow
        Write-Host "  cd backend; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e `".[dev]`"" -ForegroundColor Yellow
        return
    }
    Push-Location (Join-Path $root 'backend')
    & $venvPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
    Pop-Location
}

function Start-Frontend {
    Push-Location (Join-Path $root 'frontend')
    # 用 cmd 包一层：部分机器上 PowerShell 执行策略会拦住 pnpm.ps1
    if (-not (Test-Path 'node_modules')) { cmd /c "pnpm install" }
    cmd /c "pnpm dev"
    Pop-Location
}

switch ($Target) {
    'backend' { Start-Backend }
    'frontend' { Start-Frontend }
    'both' {
        # 用系统自带的 powershell 而不是 pwsh：pwsh（PowerShell 7）不一定装了，
        # 只装 5.1 的机器上这一行会直接失败。执行策略也一并传进去，
        # 免得新窗口里又被拦一次。
        Start-Process powershell -ArgumentList @(
            '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, 'backend'
        )
        Start-Frontend
    }
}
