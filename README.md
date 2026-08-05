# RAG 知识库系统

本机多用户 RAG（检索增强生成）知识库：在一台机器上为多个用户提供私有文档管理、检索与问答。

- `rag-service/` — Python FastAPI 后端（SQLAlchemy + MySQL + Chroma + 模型中转）
- `web/` — Vue 3 + TypeScript + Element Plus 前端（Vite）

业务术语与产品边界见 [CONTEXT.md](CONTEXT.md)，架构决策见 [docs/adr/](docs/adr/)。

## 快速启动

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
