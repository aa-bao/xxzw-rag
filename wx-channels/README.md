# wx-channels（微信视频号下载器）

本项目嵌入的微信视频号下载器 Linux 版，来自
<https://github.com/ltaoo/wx_channels_download>。

当前使用官方 Linux release 二进制（`wx_video_download`，静态链接），
以无头 `server` 模式运行，向 RAG 服务提供本地 HTTP API：

- API / Web UI：`http://127.0.0.1:2022`
- 代理端口：`2023`（默认配置未启用代理）
- MCP：`http://127.0.0.1:2022/mcp`

## 启动

本地开发/一键启动已包含该服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-project.ps1
```

Docker Compose 全栈：

```powershell
docker compose up -d --build
```

## 配置

首次启动会在数据卷 `/data` 中生成 `config.yaml`（来自
`wx-channels/config.docker.yaml`）。

### 视频号分享链接解析（必配）

RAG 的“输入链接解析视频号”依赖元宝（yuanbao.tencent.com）Cookie。
获取方式：浏览器登录 <https://yuanbao.tencent.com>，F12 -> Network
复制任意请求的 `Cookie` 请求头，然后：

- 前端方式：打开 `agent设置` → `Cookie 设置` → `元宝 Cookie` 粘贴保存。
- 脚本方式：
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure-wx-cookie.ps1
  ```

脚本会把 Cookie 直接写入共享目录 `rag-service/wx-cookies/cookies.json`，无需重启即可生效。

也可以手动编辑容器数据卷中的 `config.yaml` 的 `cloudflare.sphCookie`
后重启服务：

```bash
docker compose restart wx-channels
```

## 手动构建镜像

```powershell
docker compose build wx-channels
```

## 数据

- `/data/config.yaml`：运行时配置
- `/data/data.db`：SQLite 数据
- `/data/Downloads`：下载文件

## 说明

- 二进制为 32 位 x86 静态链接程序，需运行在 x86_64 Linux 容器/主机上。
- 容器默认关闭系统代理（`proxy.enabled: false`），仅提供 HTTP API；
  如需完整代理/微信客户端抓取能力，请按上游 Docker/Webtop 文档另行部署。
