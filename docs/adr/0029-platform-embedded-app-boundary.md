---
status: accepted
---

# 平台嵌入式应用接入边界（SSO、会话、数据隔离、Conformance）

本仓库按《项目开发/08-独立项目应用与单仓库多组件开发规范》《10-项目应用前端嵌入、SSO与权限规范》
完成 `EMBEDDED_APP` 接入：前端与后端是平台 Controller 独立部署的两个组件，不再由 FastAPI 单镜像
托管 Vue（与 ADR-0019 的本地发布形态并存：`docker-compose.yml` 仍保留本地一体部署，平台发布走
`web/Dockerfile` + `rag-service/Dockerfile` 双组件）。

## 关键决策

1. **身份事实来源是平台**：`ProjectPrincipal`（`rag-service/src/platform/principal.py`）是请求级
   身份视图，平台 ID（tenantId/userId/departmentId）一律为字符串（19 位雪花值不丢精度）。
   TEST/PRODUCTION 只接受平台项目会话（`rag_database_session` HttpOnly Cookie），禁止回退本地
   `rag_session`；LOCAL 模式按角色映射权限字符以兼容原有账号。
2. **会话 30 分钟 / 5 分钟刷新**：`PlatformSessionService`（`src/platform/sessions.py`）管理独立
   会话；认证依赖在刷新窗口内调用控制面刷新权限，停用/撤销失败关闭（401），控制面暂不可用
   保守保留会话。
3. **数据隔离**：`src/db/scope.py` 统一生成租户 + dataScope 过滤条件。RESTRICTED 且两数组皆空
   返回空集（失败关闭，禁止回退全量/本部门）。创建记录时服务端写入 tenant/department/owner，
   拒绝请求体覆盖；后台任务（DocumentJob/IngestRun）归属从显式来源（文档行/请求身份）传播，
   无隐式默认部门。
4. **Controller 密钥**：生产只从 `/run/tyt-rpa/secrets/` 按文件名读取（一个文件一个值），
   含控制面服务身份、HMAC 密钥、TLS CA 与数据库运行凭据（DATABASE_HOST/PORT/SCHEMA/
   RUNTIME_USERNAME/RUNTIME_PASSWORD/TLS_CA_CERTIFICATE）；非密钥标识走 `RPA_*` 环境变量。
   HMAC canonical string 顺序严格固定（版本/clientId/方法/路径/毫秒时间戳/nonce/幂等键/
   sha256(body)），禁止跳过 TLS 校验，401/403 不重试。
5. **Conformance 无数据库模式**：`RPA_CONFORMANCE_MODE=true` 时不创建数据库引擎、不探测模型、
   不启动入库 Worker；`contracts/platform-conformance-v1.json` 固定 13 个黑盒场景，进程内 Map
   提供跨请求幂等；`TEST_*` 夹具只从环境读取，日志经 `SecretRedactionFilter` 脱敏。
6. **Task JWT 与幂等**：`src/platform/tasks.py` 校验 iss/aud/env/app/executor/task/attempt/claim/
   expiry/status；旧 Claim/取消/过期/错误 audience 拒绝；`businessRequestId + Idempotency-Key`
   防重复创建，结果重复提交返回同一 resultId；未知副作用不自动重放。
7. **无 Windows 执行器**：本应用无 executor 组件，清单不声明 `executors`，Task 场景仅覆盖
   G5 的 PROJECT 侧边界（HMAC 验签 + Task JWT 校验 + 幂等 stub），不伪造执行器能力。

## 接入状态

仓库内可实现部分已全部实现并通过本地门禁；发布（TEST/PRODUCTION）、Registry 推送、Cosign
签名与真实主系统黑盒验收需要平台侧 Controller 凭据，按《09-项目发布、Controller生命周期与回滚规范》
由平台侧发起，本仓库不声称"生产已验收"。
