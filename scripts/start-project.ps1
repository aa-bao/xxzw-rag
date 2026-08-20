# Start RAG project in local dev mode (MySQL container + backend + Vite frontend).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-project.ps1
#
# Idempotent: skips components that are already healthy.
# URLs after start:
#   Frontend: http://localhost:5173
#   Backend:  http://127.0.0.1:8000
#   API proxy: http://localhost:5173/api -> http://127.0.0.1:8000

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Definition))
$WebDir = Join-Path $Root "web"
$RagDir = Join-Path $Root "rag-service"
$EnvPath = Join-Path $Root ".env"

function Test-Url([string]$Url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-Url([string]$Url, [int]$TimeoutSeconds = 90, [string]$Label) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-Url $Url) {
            Write-Host "[ok] $Label is up: $Url"
            return $true
        }
        Write-Host "[wait] $Label not ready yet: $Url"
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    Write-Error "$Label failed to become ready within ${TimeoutSeconds}s: $Url"
}

# 1. .env
if (-not (Test-Path $EnvPath)) {
    throw "Missing .env file: $EnvPath (copy .env.example first)"
}
Write-Host "[1/5] Load .env"

# 2. MySQL via Docker Compose (only the mysql service; backend/frontend run on host)
Write-Host "[2/5] Start MySQL container"
docker compose -f (Join-Path $Root "docker-compose.yml") up -d mysql
if ($LASTEXITCODE -ne 0) { throw "docker compose up mysql failed" }
$mysqlName = docker compose -f (Join-Path $Root "docker-compose.yml") ps -q mysql
if (-not $mysqlName) { throw "MySQL container not found" }

# 3. Wait for MySQL health, then run migrations
Write-Host "[3/5] Wait MySQL healthy and run Alembic migration"
$deadline = (Get-Date).AddSeconds(90)
do {
    $health = docker inspect -f "{{.State.Health.Status}}" $mysqlName 2>$null
    if ($health -eq "healthy") { break }
    Write-Host "[wait] MySQL health=$health"
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)
if ($health -ne "healthy") { throw "MySQL did not become healthy" }
Write-Host "[ok] MySQL healthy"

# Load .env into process for alembic (alembic.ini has a placeholder URL).
Get-Content $EnvPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $key, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim().Trim('"'), "Process")
    }
}
Push-Location $RagDir
try {
    .\.venv\Scripts\python.exe -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed" }
} finally {
    Pop-Location
}

# 4. Backend
Write-Host "[4/5] Start backend if not already running"
if (Test-Url "http://127.0.0.1:8000/platform/health") {
    Write-Host "[ok] Backend already running"
} else {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RagDir "start_local.ps1")
    if ($LASTEXITCODE -ne 0) { throw "start_local.ps1 failed" }
    Wait-Url "http://127.0.0.1:8000/platform/health" 60 "Backend"
}

# 5. Frontend
Write-Host "[5/5] Start frontend if not already running"
if (Test-Url "http://localhost:5173") {
    Write-Host "[ok] Frontend already running"
} else {
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run dev > vite.dev.log 2>&1" `
        -WorkingDirectory $WebDir -WindowStyle Hidden -PassThru
    Write-Host "[start] Frontend launcher PID $($proc.Id)"
    Wait-Url "http://localhost:5173" 90 "Frontend"
}

Write-Host ""
Write-Host "Project is running:"
Write-Host "  Frontend : http://localhost:5173"
Write-Host "  Backend  : http://127.0.0.1:8000"
Write-Host "  API proxy: http://localhost:5173/api -> http://127.0.0.1:8000"
Write-Host "  Logs     : rag-service/uvicorn.local.out.log, web/vite.dev.log"
