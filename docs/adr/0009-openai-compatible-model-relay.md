# 模型调用统一经过 OpenAI-compatible 本地中转

Embedding 与 Chat 请求统一发送到可配置的本地模型中转服务，中转接口遵循 OpenAI-compatible 协议并由配置指定模型名。RAG 系统不识别上游真实厂商，也不实现多厂商客户端；只有未来出现第二种真实协议时才引入 Adapter，模型中转密钥仅通过环境变量注入。
