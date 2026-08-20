# RAG 知识库系统

本机多用户 RAG（检索增强生成）知识库：在一台机器上为多个用户提供私有文档管理、检索与问答。

- `rag-service/` — Python FastAPI 后端（SQLAlchemy + MySQL + Chroma + 模型中转）
- `web/` — Vue 3 + TypeScript + Element Plus 前端（Vite，生产由非 root Nginx 提供）
- `rpa-application.yaml` — TYT RPA 独立应用清单；资源与副作用见 `RESOURCE.md`

业务术语与产品边界见 [CONTEXT.md](CONTEXT.md)，架构决策见 [docs/adr/](docs/adr/)。

## 快速启动

> Agent / 人工通用 SOP：见 [docs/项目启动SOP.md](docs/项目启动SOP.md)。
> 本机开发一键启动（幂等）：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-project.ps1`

### 1. 配置环境变量

```bash
cp .env.example .env
```

按需修改 `.env`：

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | MySQL 连接串（如 `mysql+asyncmy://root:pw@127.0.0.1:3306/rag`） |
| `MODEL_RELAY_BASE_URL` / `MODEL_RELAY_API_KEY` | 模型中转服务地址与密钥 |
| `EMBEDDING_MODEL` / `CHAT_MODEL` | 嵌入与对话模型名 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | 独立嵌入端点（可选，默认复用中转服务） |

### 2. 数据库迁移

```bash
cd rag-service
.venv/Scripts/python -m alembic upgrade head   # Windows
uv run alembic upgrade head                    # Linux/macOS
```

### 3. 启动后端

```bash
cd rag-service
.venv/Scripts/python -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

> 注意：`app.py` 只暴露 `create_app()` 工厂函数，uvicorn 必须加 `--factory` 参数。

### 4. 启动前端

```bash
cd web
npm install
npm run dev
```

前端开发服务器默认监听 5173，`/api` 代理到后端 8000。

## 开发模式：跳过登录

本地调试不想每次登录时，同时打开前后端两个开关（不设置即恢复正式认证，生产不受影响）：

| 端 | 开关 | 效果 |
|----|------|------|
| 后端 | 环境变量 `DEV_AUTH_BYPASS=1` 且 `APP_ENV=development`（默认） | `require_user` 直接返回固定用户 ID（默认 `1`，可用 `DEV_AUTH_BYPASS_USER_ID` 覆盖），所有受保护接口免登录；启动时会输出 `DEV_AUTH_BYPASS active` 警告日志 |
| 前端 | `web/.env.development` 中 `VITE_DEV_BYPASS_AUTH=true`（仅 vite dev 生效） | 自动以 `preview` 用户进入工作区；登录页额外显示「开发模式（跳过登录）」按钮 |

安全设计：后端开关同时要求 `APP_ENV` 为开发环境（默认值），生产部署（`APP_ENV=production`）即使误设 `DEV_AUTH_BYPASS=1` 也不会生效；绕过用户必须在数据库中真实存在（启动后首个请求校验，不存在时返回 `DEV_AUTH_BYPASS_USER_NOT_FOUND`）。登录 / 登出接口本身不受开关影响。

## 测试

```bash
cd rag-service
.venv/Scripts/python -m pytest

cd ../web
npm test
```

## TYT RPA 平台接入

平台探针：`GET /platform/health`、`GET /platform/readiness`、`GET /platform/version`（版本元数据
来自 `RPA_*` 环境变量，不含密钥）。前端以相对路径构建（`basePathMode: RELATIVE`），支持部署在
未知同源子路径，`embedded=true` 时隐藏自身全局侧栏/退出入口/用户管理入口。

已实现（本仓库内）：

- **平台 SSO**：`POST /platform/sso/bootstrap` 以表单 `code`/`state`/`codeVerifier`/`redirectUri`
  （可选 `route`，仅接受同源相对路径）兑换一次性授权码 + PKCE，建立独立 `rag_database_session`
  HttpOnly Cookie 会话（最长 30 分钟，至少每 5 分钟向控制面刷新权限；停用/权限撤销失败关闭）。
- **身份与权限**：`ProjectPrincipal` 请求级身份；平台 ID 全部为字符串（19 位雪花值不丢精度）；
  后端逐接口校验 `rag-database:*` 权限字符（真实 403）。TEST/PRODUCTION 只接受平台项目会话。
- **数据隔离**：租户 + dataScope（`ALL`/`RESTRICTED`）过滤；`RESTRICTED` 空集合返回空集；
  创建记录服务端写入 tenant/department/owner，拒绝请求体覆盖；后台任务归属显式传播。
- **Controller 密钥**：生产从 `/run/tyt-rpa/secrets/` 按文件名读取（控制面身份、HMAC、TLS CA、
  数据库运行凭据），非密钥标识走 `RPA_*` 环境变量；HMAC canonical string 顺序与规范严格一致。
- **Conformance**：`RPA_CONFORMANCE_MODE=true` 无数据库/无模型启动，`contracts/platform-conformance-v1.json`
  固定 13 个黑盒场景（SSO/HMAC/Task JWT/幂等/脱敏），进程内 Map 幂等。
- **CI**：`.github/workflows/ci.yml` 跑测试/构建/凭据扫描并生成机器可读 `gate-report.json`
  （scripts/generate-gate-report.py，无凭据时镜像推送失败关闭，绝不伪造 Digest/签名）。

当前状态：**仓库内门禁完成、TEST 可接入**。发布动作（TEST/PRODUCTION Release、Registry 推送、
Cosign 签名、真实主系统黑盒验收）需要平台侧 Controller 凭据，由平台侧发起；在真实发布与黑盒
验收通过前不得宣称"生产已上线"。本地 Compose 会分别构建前端与后端组件，统一从
`http://127.0.0.1:8000` 访问；Conformance 验证用 `docker compose -f docker-compose.yml
-f docker-compose.conformance.yml up rag-conformance`。
