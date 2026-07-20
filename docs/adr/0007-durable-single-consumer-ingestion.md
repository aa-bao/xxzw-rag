# 文档处理使用 MySQL 持久化队列与单消费者

上传与删除请求只创建 MySQL 文档任务，由 FastAPI 进程内的单个后台消费者顺序执行入库或清理。任务状态持久化并在启动时恢复，入库以文档标识和稳定 chunk 标识保持幂等；MVP 不引入 Celery 或 Redis，并固定使用单个 Uvicorn worker，避免嵌入式 ChromaDB 的多进程写冲突。
