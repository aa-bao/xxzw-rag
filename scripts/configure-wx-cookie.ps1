# 配置 wx-channels 的元宝（yuanbao.tencent.com）Cookie，用于视频号分享链接解析。
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-wx-cookie.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-wx-cookie.ps1 -Cookie "name1=value1; name2=value2"
#
# Cookie 获取：浏览器打开 https://yuanbao.tencent.com 并登录，
# F12 -> Network -> 任选一个请求 -> Request Headers -> Cookie 全选复制。
# 脚本会把 Cookie 写入容器 /data/cookies.json，无需重启即可生效。

param(
    [string]$Cookie
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Definition))
$ComposeFile = Join-Path $Root "docker-compose.yml"

if ([string]::IsNullOrWhiteSpace($Cookie)) {
    $Cookie = Read-Host "Enter yuanbao.tencent.com Cookie (from browser DevTools -> Request Headers)"
}
$Cookie = $Cookie.Trim()
if ([string]::IsNullOrWhiteSpace($Cookie)) {
    throw "Cookie must not be empty"
}

$containerId = docker compose -f $ComposeFile ps -q wx-channels
if (-not $containerId) {
    throw "wx-channels container is not running; run scripts/start-project.ps1 first"
}

$tmpJson = Join-Path $env:TEMP ("wx-cookies-" + [guid]::NewGuid().ToString("N") + ".json")
try {
    # 用 Python 从 stdin 读取 Cookie 并生成 cookies.json，避免 Cookie 出现在命令行/进程列表。
    $json = $Cookie | python -c "import sys,json; parts=[p.strip() for p in sys.stdin.read().strip().split(';') if p.strip()]; print(json.dumps([{'name':p.split('=',1)[0].strip(),'value':p.split('=',1)[1].strip(),'domain':'.tencent.com','path':'/','secure':True,'httpOnly':False,'sameSite':'Lax','expires':-1} for p in parts if '=' in p], ensure_ascii=False, indent=2))"
    if (-not $json) {
        throw "Cookie parse failed; expected format: name=value; name=value"
    }
    [System.IO.File]::WriteAllText($tmpJson, $json, [System.Text.UTF8Encoding]::new($false))

    docker cp $tmpJson "${containerId}:/data/cookies.json"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to write /data/cookies.json into wx-channels container"
    }

    Write-Host "[ok] Written wx-channels /data/cookies.json"
    Write-Host "      The service reads it immediately; no restart required."
    Write-Host "      If parsing still fails, the cookie may be expired or missing a valid yuanbao.tencent.com session."
} finally {
    if (Test-Path $tmpJson) {
        Remove-Item -Force $tmpJson
    }
}
