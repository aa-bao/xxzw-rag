# 项目说明

RAG 知识库系统。前端位于 `web/`，后端服务位于 `rag-service/`，使用 Docker 编排。

## 识图能力

底层模型不具备原生识图能力。遇到图片时，**不要用 Read 工具**，改用 ModLens 视觉桥（Google Gemini）：

```
npx @liustack/modlens analyze -i "<图片路径|URL>" --prompt "用中文描述这张图片"
```

### 触发场景

- 用户分享图片路径（本地或网络 URL）
- 消息中出现 "Saved attachments:" 并列出图片
- 用户要求分析、描述、识别图片内容

### 配置

- 识图服务为 ModLens + Google Gemini，Key 在用户主目录 `~/.modlens/config.json`
  （`provider: gemini-api`，默认代理 `http://127.0.0.1:7897`）
- 检查配置是否就绪：`npx @liustack/modlens doctor`
- 需 Node ≥ 22.19（本机 v24.18.0 满足）
- 若 npx 报 EPERM（npm 缓存目录不可写），先设置 `npm_config_cache` 到可写目录再执行

用户直接发图片即可自动识图，无需手动打命令。
