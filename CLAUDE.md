# 项目说明

RAG 知识库系统。前端位于 `web/`，后端服务位于 `rag-service/`，使用 Docker 编排。

## 识图能力

底层模型不具备原生识图能力。遇到图片时，**不要用 Read 工具**，改用 `vision.js`：

```
node vision.js "<图片路径>" "用中文描述这张图片"
```

### 触发场景

- 用户分享图片路径（本地或网络 URL）
- 消息中出现 "Saved attachments:" 并列出图片
- 用户要求分析、描述、识别图片内容

### 配置

- 识图服务为阿里云百炼 Qwen，Key 与模型名在项目根目录 `.env` 中
  （`DASHSCOPE_API_KEY` / `VISION_MODEL`，`.env` 已被 git 忽略）
- 如需换服务，修改 `vision.js` 顶部的 `DASHSCOPE_BASE_URL` 与 `.env` 中的模型名即可

用户直接发图片即可自动识图，无需手动打命令。
