"""视频解析 agent（quick-watch 集成）。

将 E:\dev\project\quick-watch 的 quick-watch skill 能力封装为网页可用的
REST API：提交视频 URL 或本地文件 → 异步下载/转写（Qwen3-ASR Flash）/
抽帧 → 返回转录、关键帧、成本等结构化结果。

实现方式：本机 Python subprocess 调用 quick_watch.py（quick-watch 依赖
本机 dashscope/yt-dlp/ffmpeg 环境，与 Docker 化的 rag-service 解耦）。
"""
