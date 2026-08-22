# Start an SSH tunnel from this Windows machine to the remote server's dsh web UI.
# Usage: powershell -ExecutionPolicy Bypass -File scripts/start-dsh-tunnel.ps1
# Then open http://127.0.0.1:18080 in your local browser.

$ErrorActionPreference = 'Stop'

$remoteHost = '115.159.227.197'
$remoteUser = 'root'
$localPort = 18080
$remotePort = 3080

# Already listening? Assume the tunnel is up.
$existing = Get-NetTCPConnection -LocalPort $localPort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "dsh tunnel already running: http://127.0.0.1:$localPort"
    exit 0
}

# NOTE: The remote dsh web server must already be running on port 3080.
# To start it remotely: ssh root@115.159.227.197 "nohup dsh web >/var/log/dsh-web.log 2>&1 &"

# Start the local SSH tunnel.
$logOut = Join-Path $env:TEMP "dsh-tunnel-$localPort.out.log"
$logErr = Join-Path $env:TEMP "dsh-tunnel-$localPort.err.log"
$sshArgs = @(
    '-o', 'BatchMode=yes',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-N',
    '-L', "127.0.0.1:${localPort}:127.0.0.1:${remotePort}",
    "${remoteUser}@${remoteHost}"
)

$proc = Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -WindowStyle Hidden `
    -RedirectStandardOutput $logOut -RedirectStandardError $logErr -PassThru

Start-Sleep -Seconds 3
$check = Get-NetTCPConnection -LocalPort $localPort -State Listen -ErrorAction SilentlyContinue
if (-not $check) {
    Write-Host "Tunnel failed to start. stderr:"
    Get-Content $logErr -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "dsh tunnel ready: http://127.0.0.1:$localPort (PID $($proc.Id))"
