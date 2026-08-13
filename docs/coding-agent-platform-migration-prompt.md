# RAG Database 全量平台接入改造——Coding Agent 执行提示词

你正在接手仓库：

```text
E:\dev\project\rag-database
```

规范仓库：

```text
E:\dev\project\project-development-specification
```

## 最终目标

按照规范仓库完成 `rag-database` 的生产级 `EMBEDDED_APP` 接入，包括：

1. 平台 SSO：一次性授权码、state、PKCE、跨应用/跨租户/过期/重复兑换拒绝。
2. 项目独立会话：专属 HttpOnly/Secure Cookie、最长 30 分钟、至少每 5 分钟刷新平台权限。
3. 身份和授权：所有平台 ID 保持字符串；后端逐接口校验权限字符。
4. 数据隔离：所有业务记录按 tenant、department、owner 归属；严格执行 `dataScope`。
5. Controller 配置：生产密钥只从 `/run/tyt-rpa/secrets/` 读取；控制面使用 HMAC、Nonce、毫秒时间戳和私有 CA。
6. Conformance：无数据库、无模型服务也能启动，并支持规范固定的 13 个黑盒场景。
7. 任务闭环：Task JWT、过期/取消/旧 Claim 拒绝，以及业务请求和结果幂等。
8. CI 交付：不可变前后端镜像、Digest、SBOM、签名、契约 Hash、机器可读门禁报告。
9. TEST/PRODUCTION 隔离、发布与回滚文档和自动化验证入口。

必须完成所有本仓库内可实现和可验证的工作。需要真实主系统、Controller、证书、Registry 或生产权限的步骤，必须提供可执行脚本/命令和明确外部依赖，禁止伪报“已上线”或“生产验收通过”。

## 首要规则

- 当前工作区很脏，包含用户原有未提交功能。禁止 reset、checkout、覆盖或删除无关改动。
- 先运行 `git status --short` 和 `git diff --check`，记录现状。
- 仓库存在 `.codegraph/`。理解或定位代码时先使用：

  ```powershell
  codegraph explore "你的问题"
  ```

- 涉及 FastAPI、SQLAlchemy、Alembic、Vue Router、Vite 等当前 API 时，遵循仓库 `AGENTS.md`，先用 `ctx7` 获取当前文档。
- 修改文件必须使用 `apply_patch`。
- 不得把真实密钥、Token、Cookie、证书、生产地址或数据库连接串写入 Git、日志、报告和测试快照。
- 不得仅在前端隐藏按钮；权限必须在后端强制执行。
- 不得用整数表示平台 `tenantId`、`userId`、`departmentId` 或 `dataScope` ID。
- `dataScope.mode=RESTRICTED` 且两个 ID 数组为空时必须返回空集，禁止回退到全量或本部门。

## 权威阅读顺序

完整阅读下列文件，不能只看标题或摘要：

```text
项目开发/08-独立项目应用与单仓库多组件开发规范.md
项目开发/09-项目发布、Controller生命周期与回滚规范.md
项目开发/10-项目应用前端嵌入、SSO与权限规范.md
项目开发/11-统一黑盒契约门禁与生产验收规范.md
项目开发/12-前端页面设计规范.md
```

若能访问主系统机器契约，主系统 `contracts/` 优先级最高。特别注意：当前规范只给出了 SSO 流程和 HMAC 规则，没有在本地规范中冻结 SSO 兑换 API 路径。必须从主系统 OpenAPI/契约确认真实路径和请求响应；找不到时保留单一适配器和明确 TODO，不得把猜测路径宣称为权威契约。

## 已完成且曾验证通过的第一阶段

以下内容已实现：

- 根 `rpa-application.yaml`、`RESOURCE.md`。
- `contracts/openapi/platform-v1.yaml`。
- `/platform/health`、`/platform/readiness`、`/platform/version`。
- Vite 相对 base、Vue Router 应用根路径、相对 API/下载 URL。
- `embedded=true` 时隐藏自身全局侧栏和退出入口。
- 独立 `web/Dockerfile`、非 root Nginx 代理和后端 Dockerfile。
- Compose 健康检查与前后端组件拆分。

在第二阶段开始前，曾验证：

```text
后端 296 passed
前端 101 passed
Vite build PASS
docker compose config --quiet PASS
Impeccable detector 返回 []
```

这些结果发生在第二阶段 WIP 写入之前，必须重新跑，不能沿用结论。

## 中断时已落盘但尚未测试的 WIP

以下是半成品，不要默认正确：

```text
rag-service/src/platform/config.py
rag-service/src/platform/signing.py
rag-service/src/platform/identity.py
rag-service/src/platform/client.py
rag-service/src/platform/sessions.py
rag-service/src/api/router_platform.py
rag-service/src/db/models.py
rag-service/src/db/migrations/versions/0006_platform_identity.py
rag-service/tests/test_platform/test_platform_core.py
rag-service/src/api/app.py
```

当前已知情况：

- Python `compileall` 通过，`git diff --check` 通过。
- 尚未运行第二阶段单测、迁移测试或全量测试。
- `ControlPlaneClient` 当前暂用：

  ```text
  /api/rpa/project-applications/sso/exchange
  /api/rpa/project-applications/sso/session
  ```

  这是未从本地主系统 OpenAPI 验证的适配器路径，必须确认或纠正。
- `PlatformSession` ORM、`0006_platform_identity` 唯一约束和现有本地 Session 兼容性需要复审。
- `app.py` 的 Conformance 提前返回分支需要确认异常处理、路由注册、生命周期和测试行为。
- 会话“每 5 分钟刷新”只定义了客户端方法/字段，尚未完整接入认证依赖。
- 现有 `require_user`/`require_admin` 仍主要返回整数本地用户 ID，尚未切换为平台身份和权限字符。

## 必须按此顺序实施

### 1. 先让 WIP 变绿

运行：

```powershell
cd E:\dev\project\rag-database\rag-service
.\.venv\Scripts\python.exe -m pytest tests/test_platform tests/test_api/test_health.py tests/test_db/test_migration.py -q
```

修复所有导入、Pydantic 严格字符串、Alembic、模型和 App 工厂问题。不得先继续堆功能。

### 2. 冻结平台身份抽象

建立请求级 `ProjectPrincipal`（名称可调整），至少包含：

```text
internal_user_id: int             # 仅作为本项目数据库兼容主键
tenant_id: str
external_user_id: str
department_id: str
permissions: frozenset[str]
data_scope: ALL | RESTRICTED
```

LOCAL 模式允许兼容原有账号；TEST/PRODUCTION 必须只接受平台项目会话。禁止生产回退到本地 `rag_session`。

权限至少映射：

```text
rag-database:project:view
rag-database:knowledge-base:manage
rag-database:chat:use
rag-database:settings:manage
```

用户管理页面属于本地账号体系。嵌入生产模式下应隐藏/禁用，不能让项目复制主系统角色管理。

### 3. 完成 SSO 与会话

- bootstrap 输入使用表单字段：`code`、`state`、`codeVerifier`、`redirectUri`、可选 `route`。
- `route` 必须是单前导 `/` 的同源相对路径，拒绝 `//`、scheme、host、反斜杠和 `..`。
- 正常模式使用项目服务身份向控制面兑换；使用 Controller CA 验证 TLS。
- 独立 Cookie 名不可与主系统/其他项目冲突，必须 HttpOnly、Secure，并验证 iframe 下 SameSite 策略。
- 会话最长 30 分钟；刷新间隔不超过 5 分钟；用户停用或权限撤销后失败关闭。
- 退出只清理项目会话。
- 前端嵌入启动流程必须读取 `./platform/session`，不得请求根 `/platform/session`，不得保留主系统 JWT。
- 短期凭据不得进入 query、history、Referer、日志或错误上报。

### 4. 数据库兼容迁移与数据隔离

不要把所有现有整数 FK 一次性粗暴替换成字符串。采用 Expand/Contract：

- 保留内部自增主键用于现有关系。
- 为用户与业务根实体增加 tenant、department、external owner 字段和索引/约束。
- 平台身份映射到内部用户记录，但平台是生产身份事实来源。
- 对知识库、文档、会话、映射模板、查询日志及后台任务逐项审计归属传播。
- 所有读取、修改、删除同时校验 tenant 和 dataScope；不能只靠 `owner_user_id`。
- 创建记录时由服务端写入 tenant/department/owner，拒绝请求体覆盖。
- 后台任务归属必须来自显式配置或 Task JWT，不能写隐式默认部门。
- 对 19 位 ID 和 `RESTRICTED + 空集合` 写集成测试。

### 5. Controller 密钥和控制面客户端

生产从以下目录读取，一个文件一个值：

```text
/run/tyt-rpa/secrets/
```

至少支持：

```text
RPA_PROJECT_SERVICE_CLIENT_ID
RPA_PROJECT_SERVICE_SECRET
RPA_PROJECT_SERVICE_SECRET_VERSION
RPA_CONTROL_PLANE_BASE_URL
CONTROL_PLANE_TLS_CA_CERTIFICATE
DATABASE_HOST
DATABASE_PORT
DATABASE_SCHEMA
DATABASE_RUNTIME_USERNAME
DATABASE_RUNTIME_PASSWORD
DATABASE_TLS_CA_CERTIFICATE
```

非密钥标识从环境变量读取：

```text
RPA_APP_KEY
RPA_ENVIRONMENT
RPA_RELEASE_VERSION
RPA_SOURCE_COMMIT
RPA_CONFIG_VERSION
RPA_COMPONENT_KEY
PORT
```

HMAC canonical string 顺序必须严格为：

```text
签名版本
clientId
大写 HTTP 方法
含 query 的完整路径
毫秒时间戳
nonce
幂等键
sha256(body)
```

禁止跳过 TLS 校验。401/403 不重试；5xx/网络错误只做有限退避。

### 6. Conformance 无数据库模式

`RPA_CONFORMANCE_MODE=true` 时：

- 配置层数据库和模型依赖为空，而不是填假 localhost。
- 启动不得创建数据库引擎、探测模型或启动入库 Worker。
- health/readiness/version 可用。
- 需要跨请求幂等的场景用进程内 Map 保存首次结果。
- stub 只把 `TEST_AUTH_CODE` 当合法 code；其他 code 一律 401。
- 所有 `TEST_*` 仅从环境读取，响应和日志不得回显。

创建 `contracts/platform-conformance-v1.json`，固定包含 13 个场景：

```text
SSO_CODE_ONCE
SESSION_EXPIRED
PERMISSION_ALLOWED
PERMISSION_DENIED
TASK_JWT_VALID
TASK_JWT_STALE
TASK_JWT_CANCELLED
HMAC_VALID
HMAC_NONCE_REPLAY
HMAC_CLOCK_SKEW
TASK_IDEMPOTENCY
RESULT_IDEMPOTENCY
LOG_REDACTION
```

从规范确认 runner 所需 JSON 格式，禁止自创无法被中央门禁读取的结构。

### 7. Task JWT 与幂等

如果本应用没有 Windows executor，清单和契约必须如实表达；不要为了通过门禁伪造执行器。
若平台要求 G5 PROJECT 端点，则实现最小真实后端边界：

- Task JWT 校验 issuer/audience/environment/app/executor/task/attempt/claim/expiry/status。
- 旧 Claim、取消、过期和错误 audience 拒绝。
- `businessRequestId + Idempotency-Key` 防止重复创建。
- 结果重复提交返回同一结果 ID。
- UNKNOWN 副作用禁止自动重放业务。
- 日志统一脱敏 `TEST_SECRET_SENTINEL` 及认证头。

### 8. CI、制品与证据

新增 GitHub Actions 或仓库现有 CI 等价流程，至少完成：

- 后端测试、前端测试、类型检查、构建、契约测试、凭据扫描。
- 分别构建前端/后端不可变镜像。
- 生成镜像 Digest、SBOM、签名入口、Manifest Hash、OpenAPI/Schema Hash。
- 生成规范要求的机器报告，例如：

  ```json
  {
    "appKey": "rag-database",
    "commitSha": "...",
    "manifestHash": "...",
    "contractHashes": {},
    "components": [],
    "tests": {
      "unit": "PASS",
      "integration": "PASS",
      "contract": "PASS",
      "security": "PASS"
    },
    "browserStarted": false,
    "businessWriteOccurred": false,
    "secretsDetected": false
  }
  ```

- Registry、Cosign Keyless 或 Controller API 无凭据时，提供明确 CI 输入并使缺失条件失败关闭，不得写假 Digest/假签名。

### 9. 文档和清单收尾

- 更新 README、RESOURCE、OpenAPI、权限摘要、迁移与回滚说明。
- `rpa-application.yaml` 只能声明真实已实现能力。
- TEST/PRODUCTION 配置、Schema、JWT 和回调必须隔离。
- 写清 G6 无副作用 Smoke 与 G7 受控灰度顺序。
- 旧架构文档若声称“FastAPI 单镜像托管 Vue”，应更新或增加 ADR 说明新平台组件边界。

## 最终验收命令

至少运行：

```powershell
cd E:\dev\project\rag-database

git diff --check
docker compose config --quiet

cd rag-service
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m pytest -q

cd ..\web
npx vitest run
npm run build

cd ..
node C:\Users\Win10\.agents\skills\impeccable\scripts\detect.mjs --json web/src/layouts/AppLayout.vue web/src/platform.ts web/src/api/client.ts
```

还要验证：

- 后端镜像可构建。
- 前端镜像可构建。
- Conformance 镜像在无数据库/无模型变量下启动，三个平台探针成功。
- 合法 SSO 一次成功、第二次失败。
- 错误 state/PKCE/cross-app/cross-tenant/expired code 均失败。
- allowed/denied/expired session 行为正确。
- 19 位 ID 不丢精度。
- RESTRICTED 空范围返回空集。
- 权限拒绝为真实 HTTP 403，过期会话为 401，依赖不可用为 503。
- 响应、日志和报告不包含夹具密钥。

## 最终汇报格式

最终回答必须包含：

1. 实际完成的能力与关键文件。
2. 数据库迁移与兼容策略。
3. 精确测试数量、构建和镜像验证结果。
4. 尚需外部主系统/Controller/Registry 执行的步骤。
5. 明确说明是否达到：本地门禁完成、TEST 可接入、PRODUCTION 可发布、真实生产已验收。

只有所有仓库内工作完成且验证通过时，才可以说“代码改造完成”。只有真实外部发布和黑盒验收成功时，才可以说“生产接入完成”。
