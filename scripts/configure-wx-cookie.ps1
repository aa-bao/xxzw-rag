# 配置 wx-channels 的元宝（yuanbao.tencent.com）Cookie，用于视频号分享链接解析。
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-wx-cookie.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-wx-cookie.ps1 -Cookie "name1=value1; name2=value2"
#
# Cookie 获取：浏览器打开 https://yuanbao.tencent.com 并登录，
# F12 -> Network -> 任选一个请求 -> Request Headers -> Cookie 全选复制。
# 脚本会把 Cookie 直接写入共享目录 rag-service/wx-cookies/cookies.json，无需 docker cp。

param(
    [string]$Cookie
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Definition))
$CookiePath = Join-Path $Root "rag-service\wx-cookies\cookies.json"
$CookieDir = Split-Path -Parent $CookiePath

if ([string]::IsNullOrWhiteSpace($Cookie)) {
    $Cookie = Read-Host "Enter yuanbao.tencent.com Cookie (from browser DevTools -> Request Headers)"
}
$Cookie = $Cookie.Trim()
if ([string]::IsNullOrWhiteSpace($Cookie)) {
    throw "Cookie must not be empty"
}

New-Item -ItemType Directory -Force -Path $CookieDir | Out-Null

# 用 Python 从 stdin 读取 Cookie 并生成 cookies.json，避免 Cookie 出现在命令行/进程列表。
$json = $Cookie | python -c "import sys,json; parts=[p.strip() for p in sys.stdin.read().strip().split(';') if p.strip()]; print(json.dumps([{'name':p.split('=',1)[0].strip(),'value':p.split('=',1)[1].strip(),'domain':'.tencent.com','path':'/','secure':True,'httpOnly':False,'sameSite':'Lax','expires':-1} for p in parts if '=' in p], ensure_ascii=False, indent=2))"
if (-not $json) {
    throw "Cookie parse failed; expected format: name=value; name=value"
}
[System.IO.File]::WriteAllText($CookiePath, $json, [System.Text.UTF8Encoding]::new($false))

Write-Host "[ok] Written $CookiePath"
Write-Host "      The service reads it immediately via the shared volume; no restart required."
Write-Host "      If parsing still fails, the cookie may be expired or missing a valid yuanbao.tencent.com session."
