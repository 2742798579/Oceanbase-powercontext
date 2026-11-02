# 一键启动后端（Windows PowerShell）
$ErrorActionPreference = "Stop"

$backend = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
Set-Location $backend

if (-not (Test-Path ".venv")) {
    Write-Host "[start] 创建虚拟环境"
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -U pip
    .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Write-Host "[start] 未找到 .env，从 .env.example 复制（默认离线哈希嵌入）"
    Copy-Item ".env.example" ".env"
}

# 读取 .env 到当前进程
Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value -and -not $value.StartsWith("#")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$seekdbPath = $env:SEEKDB_PATH
if (-not $seekdbPath) { $seekdbPath = ".\data\seekdb" }

if (-not (Test-Path $seekdbPath)) {
    Write-Host "[start] 初始化 seekdb 与预置旧账"
    .\.venv\Scripts\python.exe seed.py
}

$port = if ($env:PORT) { $env:PORT } else { "8077" }
Write-Host "[start] 后端启动于 http://0.0.0.0:$port"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port $port
