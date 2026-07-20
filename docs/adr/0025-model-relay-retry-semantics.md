# 模型中转按调用类型采用不同重试语义

Embedding 对超时、限流和服务端错误最多指数退避重试 3 次；Chat 只允许在输出首个 SSE chunk 前自动重试 1 次，一旦向客户端输出内容就不再自动重试，失败消息进入 `failed` 状态。参数、鉴权和模型不存在等客户端错误不重试，健康接口分别报告 MySQL、ChromaDB、Embedding 与 Chat 状态。
