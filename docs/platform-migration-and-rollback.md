# 平台接入数据库迁移与回滚说明

## 迁移版本

| 版本 | 内容 | 方向 |
| --- | --- | --- |
| `0006_platform_identity` | `rag_user` 增加 `platform_tenant_id`/`platform_user_id`/`platform_department_id` 与唯一约束；新增 `rag_platform_session`（项目独立会话，token_hash 唯一，按 expires_at 索引） | 仅追加 |
| `0007_platform_data_isolation` | 9 张业务表（`rag_knowledge_base`、`rag_document`、`rag_document_job`、`rag_mapping_template`、`rag_ingest_run`、`rag_conversation`、`rag_conversation_kb`、`rag_message`、`rag_query_log`）增加 `tenant_id`/`department_id` 可空列与 `tenant_id` 索引 | 仅追加 |

## Expand/Contract 策略

- 保留全部内部自增主键与既有关系（复合 FK、级联删除不变），平台字段以可空列扩展，不替换任何现有列。
- 既有 LOCAL 数据 `tenant_id` 为 `NULL`/空串：租户过滤条件对 LOCAL 身份匹配 `NULL OR ''`，旧数据无需回填即可兼容。
- 平台模式下新记录由服务端写入归属（拒绝请求体覆盖）；后台任务归属从显式来源传播（文档行/请求身份），无隐式默认部门。
- 平台身份（`PlatformSession` 的 `identity_json` 快照 + `rag_user.platform_*` 映射）是生产身份事实来源；`rag_user` 内部 ID 仅作兼容主键，不参与权限判断。

## 回滚

- 代码与容器可回滚到旧 Release；**数据库默认不执行降级脚本**（规范 09 §22）。两个迁移都是纯追加
  （Add Column + Create Table），旧版本代码忽略新列/新表即可运行，无需降级即可回滚代码。
- 若确需降级（仅限 TEST）：`alembic downgrade 0005_structured_json_ingestion` 会删除新增列与
  平台会话表；PRODUCTION 一律不执行降级，采用先回滚代码、保留 Schema 的方式。
- 回滚后必须重跑 health/readiness/version、SSO 与最小业务 Smoke（规范 09 §24）。

## 一致性校验

```bash
cd rag-service
.\.venv\Scripts\python.exe -m alembic heads        # 应为 0007_platform_data_isolation
.\.venv\Scripts\python.exe -m alembic history     # 无分叉、无悬空
```

## TEST / PRODUCTION 隔离

- 两环境独立：容器与路由、Schema 与数据库运行账号、服务身份与 HMAC 密钥、项目会话密钥、
  配置与 SecretVersion、回调地址与 Task JWT audience 全部按环境隔离（规范 09 §26）。
- 测试服务身份不能创建生产 Task；两环境的 JWT 与回调互相拒绝（Task JWT 校验 `env`/`app` claim）。
- 生产密钥只从 `/run/tyt-rpa/secrets/` 读取；Git 中只有 `.env.example` 占位与键名说明。
