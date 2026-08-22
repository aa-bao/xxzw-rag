# 项目启动 SOP（Agent 与人工通用）

RAG 知识库系统开发环境启动标准流程。**默认采用本地开发模式**：MySQL 跑 Docker 容器，后端 FastAPI 与前端 Vite 跑在宿主机，保留热更新、日志直接可见，启动最快。

> 生产/类生产验证请改用 Docker Compose 全栈（见下文「备选：Docker Compose 全栈」）。

## 0. 前置检查

- 已安装 Docker Desktop 且 Docker daemon 正在运行。
- 已存在 `.env`（不存在则先 `cp .env.example .env`）。
- 依赖已就绪：
  - `rag-service/.venv/` 存在（否则 `cd rag-service && uv sync --extra dev` 或按 `pyproject.toml` 安装）
  - `web/node_modules/` 存在（否则 `cd web && npm install`）

## 1. 一键启动（推荐，Agent 直接用这个）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-project.ps1
```

脚本幂等，已运行的服务会跳过。会自动拉起 `mysql` 与 `wx-channels` 两个 Docker 服务，再启动后端/前端；看到末尾 `Project is running:` 即全部就绪。完成后：

- 前端：<http://localhost:5173>
- 后端：<http://127.0.0.1:8000>
- API 代理：<http://localhost:5173/api> → `127.0.0.1:8000`
- 微信视频号下载器 API/UI：<http://127.0.0.1:2022>（Docker 容器 `wx-channels`）

## 2. 手动启动（脚本不可用时逐条执行）

### 2.1 启动 MySQL

```powershell
docker compose up -d mysql
```

等待健康：

```powershell
docker inspect -f "{{.State.Health.Status}}" rag-database-mysql-1
# 期望输出 healthy
```

### 2.2 启动微信视频号下载器（wx-channels）

```powershell
docker compose up -d wx-channels
```

等待 API：

```powershell
curl.exe http://127.0.0.1:2022/
# 返回 HTML 页面 / 200 即就绪
```

配置视频号解析所需元宝 Cookie（可选，但解析分享链接前必须）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-wx-cookie.ps1
```

### 2.3 执行数据库迁移

⚠️ **必须先加载 `.env`，再跑 `alembic`。** `alembic.ini` 里的 `sqlalchemy.url` 是占位符；不加载 `.env` 会报 `Access denied for user 'unused'`。

```powershell
# 在仓库根目录执行：把 .env 加载进当前 PowerShell 进程
Get-Content .env | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
    $key, $value = $line.Split('=', 2)
    [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim().Trim('"'), 'Process')
  }
}

cd rag-service
.\.venv\Scripts\python.exe -m alembic upgrade head
```

迁移成功时无错误输出，或仅打印 `INFO [alembic.runtime.migration] ...`。

### 2.4 启动后端

已有脚本会加载 `.env` 并开启开发免登录（`DEV_AUTH_BYPASS=1`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File rag-service/start_local.ps1
```

验证：

```powershell
curl.exe http://127.0.0.1:8000/platform/health
# {"status":"healthy"}
```

### 2.5 启动前端

```powershell
cd web
npm run dev
```

验证：

```powershell
curl.exe http://localhost:5173/api/health/live
# {"success":true,"data":{"status":"live"}}
```

## 3. 验证清单

| 检查项 | 地址 | 期望 |
|--------|------|------|
| MySQL | `docker compose ps mysql` | `healthy` |
| wx-channels | `docker compose ps wx-channels` | `healthy` |
| wx-channels API/UI | `http://127.0.0.1:2022` | 返回 HTML，状态 200 |
| 后端 | `http://127.0.0.1:8000/platform/health` | `{"status":"healthy"}` |
| 后端存活 | `http://127.0.0.1:8000/api/health/live` | `{"success":true,...}` |
| 前端 | `http://localhost:5173` | 返回 HTML，状态 200 |
| 代理 | `http://localhost:5173/api/health/live` | 同上 JSON |

## 4. 常用排查

| 现象 | 原因 / 处理 |
|------|-------------|
| `alembic` 报 `Access denied for user 'unused'` | 没有加载 `.env`，按 2.3 先加载再迁移 |
| 视频号解析失败，原始错误含“no yuanbao.tencent.com cookie”或“please initialize the client socket connection first” | wx-channels 未配置元宝 Cookie / 没有视频号页面连接；执行 `scripts/configure-wx-cookie.ps1` 配置后重试 |
| `http://127.0.0.1:5173` 连不上，但 `http://localhost:5173` 可以 | Vite 默认只绑了 IPv6 `::1`；本项目前端入口用 `localhost:5173` |
| MySQL 容器一直 `starting` | Docker Desktop 未完全就绪或端口被占用；先等 30–90 秒 |
| 后端启动后立刻退出 | 看 `rag-service/uvicorn.local.err.log`；多半是 `.env` 缺失、端口占用或数据库连不上 |
| 前端 5173 被占用 | `npm run dev` 使用 `strictPort: true`，端口被占用会失败；释放端口后重试 |
| 不想免登录 | 不要使用 `rag-service/start_local.ps1`；去掉 `.env.development` 的 `VITE_DEV_BYPASS_AUTH=true` 后手动起后端/前端 |

## 5. 备选：Docker Compose 全栈

构建并启动前端、后端、MySQL、wx-channels 四个容器（迁移在容器入口自动执行）：

```powershell
docker compose up -d --build
```

统一访问 <http://127.0.0.1:8000>。适合验证生产形态 / 门禁，但构建慢、无前端热更新；日常开发优先本地模式。

Conformance 黑盒门禁：

```powershell
docker compose -f docker-compose.yml -f docker-compose.conformance.yml up rag-conformance
```

## 6. 停止项目

```powershell
# 后端（找到占用 8000 的进程后结束）
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 前端（找到占用 5173 的进程后结束）
Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# MySQL 与 wx-channels 容器（可选，保留数据）
docker compose stop mysql wx-channels
```

## 7. 给 Agent 的提醒

- 启动项目的入口是 `scripts/start-project.ps1`，不要再绕 README 的零散命令。
- `.env` 含真实密钥，禁止打印、提交或写入日志。
- `rag-service/start_local.ps1` 会开启 `DEV_AUTH_BYPASS`，只用于本机开发。
