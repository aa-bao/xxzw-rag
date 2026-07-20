# MVP 提供跨存储一致性备份与恢复

备份必须同时覆盖 MySQL、ChromaDB 和原始文件目录。`ragctl backup` 进入维护模式、停止新写入并等待当前文档任务结束，再生成数据库导出、目录快照和带版本及校验和的 manifest；`ragctl restore` 仅在服务停止时运行，并在恢复后校验元数据、文件与 Collection 一致性。
