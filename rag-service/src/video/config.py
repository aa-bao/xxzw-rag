"""视频解析模块配置：quick-watch 脚本与本机 Python 定位。"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# quick-watch skill 根目录（脚本所在仓库）
DEFAULT_QUICK_WATCH_ROOT = Path(os.environ.get(
    "QUICK_WATCH_ROOT",
    r"E:\dev\project\quick-watch",
)).expanduser()

# quick-watch 脚本路径
QUICK_WATCH_SCRIPT = DEFAULT_QUICK_WATCH_ROOT / "scripts" / "quick_watch.py"
RENDER_REPORT_SCRIPT = DEFAULT_QUICK_WATCH_ROOT / "scripts" / "render_report.py"

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


@dataclass(frozen=True)
class VideoConfig:
    """视频解析运行配置。"""

    quick_watch_root: Path
    quick_watch_script: Path
    render_report_script: Path
    task_root: Path
    output_root: Path
    upload_dir: Path
    python: str
    dashscope_api_key: str

    @classmethod
    def load(cls) -> "VideoConfig":
        """从环境变量加载；缺省使用项目内的固定默认值。"""
        quick_root = Path(os.environ.get("QUICK_WATCH_ROOT", str(DEFAULT_QUICK_WATCH_ROOT)))
        task_root = Path(os.environ.get("QUICK_WATCH_TASK_ROOT", str(DEFAULT_TASK_ROOT)))
        output_root = Path(os.environ.get("QUICK_WATCH_OUTPUT_ROOT", str(DEFAULT_OUTPUT_ROOT)))
        upload_dir = Path(os.environ.get("QUICK_WATCH_UPLOAD_DIR", str(DEFAULT_UPLOAD_DIR)))

        # 本机 Python：优先 venv 的 python，其次系统 python
        python = os.environ.get("QUICK_WATCH_PYTHON", "")
        if not python:
            python = shutil.which("python") or sys.executable

        return cls(
            quick_watch_root=quick_root,
            quick_watch_script=quick_root / "scripts" / "quick_watch.py",
            render_report_script=quick_root / "scripts" / "render_report.py",
            task_root=task_root,
            output_root=output_root,
            upload_dir=upload_dir,
            python=python,
            dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        )
