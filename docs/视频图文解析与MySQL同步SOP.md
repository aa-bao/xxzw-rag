# 视频图文解析功能同步 SOP

> 适用：将本仓库 `feat/video-analize-agent` 分支的新功能（小红书视频/图文、抖音视频、MySQL 持久化、媒体播放页）快速同步到另一台服务器。
>
> 目标服务器建议：Docker + Docker Compose 已安装，能访问腾讯云 CCR 镜像仓库。

---

## 一、本次新增/变更能力

| 能力 | 说明 |
|---|---|
| 小红书视频 | 自动识别为视频，走原有视频解析链路 |
| 小红书图文 | 自动识别为图文，抓取多图、正文、作者、标签、发布时间 |
| 抖音视频 | 自动识别为视频，走原有视频解析链路 |
| 抖音图文 | ⏸ 暂缓（yt-dlp 不支持 `/note/` 链接） |
| 图文前端 | 中间展示图片/正文/摘要，右侧图文问答 |
| 视频前端 | URL 输入增加「自动识别 / 视频 / 图文」选择 |
| MySQL 持久化 | 新增 `rag_video_task` 表，保存任务状态/转录/摘要/图片/问答历史 |
| 媒体播放 | 右侧媒体面板：视频播放器、音频播放器、关键帧/图片下载 |
| 分享文案自动提取 | 粘贴带文字的分享链接会自动提取 URL |
| 多模态模型 | 系统 relay 使用 `deepseek-v4-flash-vision-exp` |
| ASR 双渠道 | 火山引擎 + 阿里云百炼（dashscope） |

---

## 二、代码/版本信息

- Git 分支：`feat/video-analize-agent`
- GitHub 远端：`origin` → `https://github.com/aa-bao/xxzw-rag.git`
- 最新提交（同步基线）：
  - `23ee437 build(rag): 使用国内 PyPI 源安装 uv 并设置 venv PATH`
  - `e1af4a3 feat(video): 合并服务器端 ASR 双渠道（dashscope/volcengine）支持`
  - `b381b9e feat(video): 支持小红书/抖音图文解析 + MySQL 视频任务持久化`

---

## 三、关键变更文件

### 后端
```text
rag-service/src/db/models.py
rag-service/src/db/repositories.py
rag-service/src/video/acquire.py
rag-service/src/video/asr.py
rag-service/src/video/router.py
rag-service/src/video/service.py
rag-service/src/video/settings.py
rag-service/src/video/summary.py
rag-service/src/db/migrations/versions/0012_video_task.py
rag-service/src/db/migrations/versions/0013_video_content_type.py
rag-service/Dockerfile
```

### 前端
```text
web/src/api/video.ts
web/src/views/VideoAnalysisView.vue
web/Dockerfile（未改，但需随仓库同步）
```

### 脚本
```text
scripts/migrate_video_data_to_mysql.py
```

---

## 四、数据库迁移

目标服务器需要执行：

```bash
alembic upgrade head
```

或依赖 `rag` 容器启动时自动执行迁移。

当前迁移链：

```text
0012_video_task                 创建视频任务 MySQL 表
0013_video_content_type         增加图文字段 content_type/post_images/post_text 等
```

验证：

```bash
alembic current
# 期望：0013_video_content_type (head)
```

---

## 五、定时/一次性数据迁移（可选）

如果目标服务器已有文件系统的视频任务数据，需要把旧数据写入 MySQL：

```bash
cd /opt/rag-database
rag-service/.venv/bin/python scripts/migrate_video_data_to_mysql.py
```

脚本会扫描：

```text
rag-service/data/video_tasks/*.json
rag-service/data/video_output/*/manifest.json
```

以 `task_id` 为唯一键 upsert 到 MySQL `rag_video_task`。

---

## 六、服务器快速同步步骤（给 Coding Agent）

### 6.1 准备

- 目标服务器有：
  - Docker + Docker Compose
  - 能访问 `ccr.ccs.tencentyun.com`
  - 已配置 CCR 登录（可直接 `docker pull`）
  - 项目目录：建议 `/opt/rag-database`
  - `.env` 已存在，且包含数据库/模型/ASR 配置

### 6.2 同步源码到目标服务器

推荐用 git 或 tar：

```bash
cd /opt/rag-database
git pull origin feat/video-analize-agent
```

如果服务器不是 git 仓库，使用 tar 同步：

```bash
# 在本地打包
tar -czf /tmp/rag-sync.tar.gz \
  rag-service/src/db/models.py \
  rag-service/src/db/repositories.py \
  rag-service/src/video/acquire.py \
  rag-service/src/video/asr.py \
  rag-service/src/video/router.py \
  rag-service/src/video/service.py \
  rag-service/src/video/settings.py \
  rag-service/src/video/summary.py \
  rag-service/src/db/migrations/versions/0012_video_task.py \
  rag-service/src/db/migrations/versions/0013_video_content_type.py \
  scripts/migrate_video_data_to_mysql.py \
  rag-service/Dockerfile

# 上传后解压
tar -xzf /tmp/rag-sync.tar.gz -C /opt/rag-database
```

### 6.3 使用 CCR 现成镜像（推荐，最快）

本地已经构建并推送的最新镜像：

```text
ccr.ccs.tencentyun.com/rag-database/rag-database-rag:latest
ccr.ccs.tencentyun.com/rag-database/rag-database-web:latest
```

目标服务器直接：

```bash
cd /opt/rag-database
docker compose pull
docker compose up -d
```

如果 `docker-compose.yml` 使用 `image:` 指向 CCR，这一条即可完成升级。

### 6.4 本地重新构建镜像（若需要）

在本地或 CI 执行：

```bash
# 后端
docker build -t ccr.ccs.tencentyun.com/rag-database/rag-database-rag:latest rag-service
docker push ccr.ccs.tencentyun.com/rag-database/rag-database-rag:latest

# 前端
docker build -t ccr.ccs.tencentyun.com/rag-database/rag-database-web:latest web
docker push ccr.ccs.tencentyun.com/rag-database/rag-database-web:latest
```

### 6.5 验证

```bash
curl -s http://127.0.0.1:8000/platform/health
# {"status":"healthy"}

docker compose ps
# mysql / rag / web 都应为 Up (healthy)

docker compose exec rag python -m alembic current
# 0013_video_content_type (head)
```

---

## 七、环境配置要求

### 7.1 `.env` 关键项

```text
# 数据库（建议使用现有 docker-compose 的 MySQL）
MYSQL_ROOT_PASSWORD=...

# 系统模型 relay（多模态）
MODEL_RELAY_BASE_URL=http://192.168.1.67:3000/v1
MODEL_RELAY_API_KEY=...
CHAT_MODEL=deepseek-v4-flash-vision-exp

# 视频 agent ASR（双渠道）
ASR_PROVIDER=volcengine
# 或阿里云百炼
# ASR_PROVIDER=dashscope
# DASHSCOPE_ASR_API_KEY=...
# ASR_PUBLIC_HOST=服务器公网IP
# ASR_PUBLIC_PORT=18081

VOLC_ASR_API_KEY=...
VOLC_ASR_MODEL=bigmodel
```

### 7.2 docker-compose 端口

百炼 ASR 需要公网暴露 `18081`：

```yaml
rag:
  ports:
    - "18081:18081"
```

---

## 八、注意事项

- 抖音图文暂缓，`/note/` 链接无法用 yt-dlp 抓取，不要指望自动识别。
- 如果目标服务器没有 CCR 登录凭据，需要先：
  ```bash
  docker login ccr.ccs.tencentyun.com
  ```
- 不要覆盖目标服务器的 `.env`，它包含数据库密码/API Key。
- 如果目标服务器 MySQL 已有数据，执行 0012/0013 迁移会自动添加新表/字段，不影响旧数据。
- 旧文件数据迁移脚本幂等，可重复执行。

---

## 九、快速验收清单

- [ ] 后端 `/platform/health` 返回 healthy
- [ ] MySQL `rag_video_task` 表存在
- [ ] Alembic 版本 `0013_video_content_type (head)`
- [ ] 前端视频分析页可看到「自动识别 / 视频 / 图文」类型选择
- [ ] 小红书图文任务可成功解析并展示图片/正文/右侧问答
- [ ] 小红书视频任务可成功解析并展示媒体播放
- [ ] 抖音视频任务可成功解析
- [ ] 问答能引用图片内容（依赖多模态 relay）
- [ ] 若需要，`scripts/migrate_video_data_to_mysql.py` 可完成旧数据迁移
