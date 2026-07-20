# 应用启动前完成单次数据库迁移

容器入口先执行 `alembic upgrade head`，成功后才启动 FastAPI；迁移失败时容器直接退出，不允许应用以不兼容 schema 运行。每个发布版本记录预期 revision，破坏性迁移要求先生成加密备份，迁移不会在 Uvicorn worker 内重复执行。
