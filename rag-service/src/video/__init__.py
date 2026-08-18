"""视频解析 agent（内建原生流水线）。

在 rag-service 内以纯 Python + asyncio 实现完整视频解析流水线：
提交视频 URL 或本地文件 → 异步下载/字幕/音频 → 语音转写
（火山引擎录音文件极速识别）/ 抽帧 → 摘要（豆包方舟 Chat）→
返回转录、关键帧、摘要、HTML 报告等结构化结果。

不再 subprocess 调用外部 quick-watch 脚本；yt-dlp / ffmpeg 为
运行时工具依赖（pyproject 声明），模型凭证由 video_setting 配置。
"""
