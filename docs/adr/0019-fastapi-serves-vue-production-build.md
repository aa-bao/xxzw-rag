# 生产发布由 FastAPI 托管 Vue 构建产物

本机发布使用多阶段镜像构建 Vue，并由 FastAPI 在同一端口提供静态页面和 `/api`。Docker Compose 只编排 RAG 服务与 MySQL，浏览器统一访问 `127.0.0.1:8000`；这消除生产态 Vite 依赖、跨域配置和跨站 Cookie 问题，Vite 仅保留给开发模式。
