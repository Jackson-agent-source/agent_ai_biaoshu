# 本地真实服务（使用 backend/.env 中的 DeepSeek 等配置，不启动 demo_trends.py）。
# 两个 PowerShell 窗口分别运行：
#   .\run-local.ps1 backend
#   .\run-local.ps1 frontend
param(
    [Parameter(Position = 0)]
    [ValidateSet('backend', 'frontend', 'tracking')]
    [string]$Service = 'backend',
    [int]$UserId = 0,
    [int]$IntervalSeconds = 3600
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

if ($Service -eq 'backend' -or $Service -eq 'tracking') {
    $pythonPath = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Missing backend/.venv. Create the Python environment and install backend/requirements.txt first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'backend\.env'))) {
        throw 'Missing backend/.env. Copy backend/.env.example and configure DeepSeek first.'
    }
    Push-Location (Join-Path $projectRoot 'backend')
    try {
        if ($Service -eq 'tracking' -and $UserId -lt 1) {
            throw 'Specify your local user ID: .\run-local.ps1 tracking -UserId 4'
        }
        $env:DJANGO_SETTINGS_MODULE = 'config.settings'
        $env:PYTHONUTF8 = '1'
        & $pythonPath manage.py migrate --noinput
        if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
        if ($Service -eq 'tracking') {
            & $pythonPath -u manage.py watch_trends --user-id $UserId --interval $IntervalSeconds
        } else {
            & $pythonPath manage.py runserver 127.0.0.1:8000 --noreload
        }
    } finally {
        Pop-Location
    }
} else {
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules\vite\bin\vite.js')) {
            throw 'Missing frontend dependencies. Run npm.cmd ci in frontend first.'
        }
        # 优先于旧的前端配置，避免残留云端地址或 8001 演示后端。
        $env:VITE_API_BASE_URL = 'http://127.0.0.1:8000/api/v1'
        & node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort
    } finally {
        Pop-Location
    }
}
