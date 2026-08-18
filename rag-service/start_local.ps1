# Start RAG backend as a detached process (dev mode, auth bypass).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File start_local.ps1
# Stop:  taskkill /PID <PID> /F

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Definition)
$Root = Split-Path -Parent $ScriptDir  # rag-database/

# 1. Load ../.env into the current process environment
$envPath = Join-Path $Root ".env"
if (-not (Test-Path $envPath)) {
    throw "Missing .env file: $envPath"
}
Get-Content $envPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $key, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim().Trim('"'), "Process")
    }
}

# 2. Dev mode: skip login (requires APP_ENV=development, same as README)
$env:DEV_AUTH_BYPASS = "1"
$env:APP_ENV = "development"

# 3. Launch uvicorn as an independent hidden process with logs on disk
$python = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$outLog = Join-Path $ScriptDir "uvicorn.local.out.log"
$errLog = Join-Path $ScriptDir "uvicorn.local.err.log"
$uvicornArgs = @("-m", "uvicorn", "src.api.app:create_app", "--factory",
                 "--host", "127.0.0.1", "--port", "8000")
$proc = Start-Process -FilePath $python -ArgumentList $uvicornArgs `
    -WorkingDirectory $ScriptDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

Write-Host "RAG backend started: PID $($proc.Id)"
Write-Host "Logs: $outLog / $errLog"
Write-Host "Health: curl http://127.0.0.1:8000/platform/health"
