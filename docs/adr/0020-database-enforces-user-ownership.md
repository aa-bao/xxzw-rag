# MySQL 使用复合外键强制用户归属

应用查询必须显式按当前 `owner_user_id` 过滤，同时 MySQL 通过 `(resource_id, owner_user_id)` 唯一键与复合外键保证文档、任务、会话和日志不能关联到其他用户的知识库。消息与引用等纯 MySQL 子记录使用级联删除；跨 ChromaDB 和文件系统的清理仍由持久化后台任务编排。
