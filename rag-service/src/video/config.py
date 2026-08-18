"""视频解析模块配置：路径与运行环境（流水线已内建，不再依赖 quick-watch 脚本）。"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# 任务状态与输出根目录（相对 rag-service 数据目录）
DEFAULT_TASK_ROOT = Path(os.environ.get(
    "QUICK_WATCH_TASK_ROOT",
    r"E:\dev\project\rag-database\rag-service\data\video_tasks",
)).expanduser()

# 网页版交付物根目录（每任务一个文件夹）
DEFAULT_OUTPUT_ROOT = Path(os.environ.get(
    "QUICK_WATCH_OUTPUT_ROOT",
    r"E:\dev\project\rag-database\rag-service\data\video_output",
)).expanduser()

# 上传的本地视频暂存目录
DEFAULT_UPLOAD_DIR = Path(os.environ.get(
    "QUICK_WATCH_UPLOAD_DIR",
    r"E:\dev\project\rag-database\rag-service\data\video_uploads",
)).expanduser()

# 历史视频库：quick-watch skill 默认输出根目录（C 盘用户目录下）
DEFAULT_LIBRARY_ROOT = Path(os.environ.get(
    "QUICK_WATCH_LIBRARY_ROOT",
    str(Path.home() / "quick-watch"),
)).expanduser()

# yt-dlp cookie 文件（平台需要新鲜会话 cookie 时使用；不存在则忽略）
DEFAULT_COOKIE_FILE = Path(os.environ.get(
    "QUICK_WATCH_COOKIE_FILE",
    str(Path(__file__).resolve().parent.parent.parent / "cookies.txt"),
)).expanduser()


@dataclass(frozen=True)
class VideoConfig:
    """视频解析运行配置（流水线内建，无外部脚本依赖）。"""

    task_root: Path
    output_root: Path
    upload_dir: Path
    library_root: Path
    cookie_file: Path | None
    python: str

    @classmethod
    def load(cls) -> "VideoConfig":
        """从环境变量加载；缺省使用项目内的固定默认值。"""
        task_root = Path(os.environ.get("QUICK_WATCH_TASK_ROOT", str(DEFAULT_TASK_ROOT)))
        output_root = Path(os.environ.get("QUICK_WATCH_OUTPUT_ROOT", str(DEFAULT_OUTPUT_ROOT)))
        upload_dir = Path(os.environ.get("QUICK_WATCH_UPLOAD_DIR", str(DEFAULT_UPLOAD_DIR)))
        library_root = Path(os.environ.get("QUICK_WATCH_LIBRARY_ROOT", str(DEFAULT_LIBRARY_ROOT)))
        cookie_file = DEFAULT_COOKIE_FILE
        if not cookie_file.is_file():
            cookie_file = None

        # 本机 Python：优先 venv 的 python，其次系统 python
        python = os.environ.get("QUICK_WATCH_PYTHON", "")
        if not python:
            python = shutil.which("python") or sys.executable

        return cls(
            task_root=task_root,
            output_root=output_root,
            upload_dir=upload_dir,
            library_root=library_root,
            cookie_file=cookie_file,
            python=python,
        )
