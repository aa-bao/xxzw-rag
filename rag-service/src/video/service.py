"""视频解析任务服务：异步执行 quick_watch.py，持久化状态与结果。

任务生命周期（文件系统状态机）：
  submitted → running → complete | failed

状态文件：<task_root>/<task_id>.json
  {
    "task_id": str,
    "source": str,            // 原始输入（URL 或本地文件路径）
    "kind": "url" | "file",
    "status": "submitted|running|complete|failed",
    "created_at": iso,
    "updated_at": iso,
    "output_dir": str | None,     // quick-watch 交付物目录（含 manifest.json / frames / transcript）
    "cache_manifest": str | None, // quick-watch 缓存 manifest 路径
    "error": str | None,
    "stage": str | None,          // 最近进度阶段
    "report": dict | None,        // quick-watch report JSON
    "transcript": str | None,     // 转录全文
    "keyframes": list,            // [{path, timestamp_seconds}]
    "cost": dict | None,          // 成本明细
    "pid": int | None,            // 运行中进程 PID
  }
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.video.config import VideoConfig

# 每任务最大运行时长（quick-watch 默认 run-budget 600s，加上抽取与渲染余量）
_TASK_TIMEOUT_SECONDS = 900

# 任务状态常量
STATUS_SUBMITTED = "submitted"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


class VideoTaskError(RuntimeError):
    """视频任务领域错误（对外以 AppError 包装）。"""


class VideoTaskManager:
    """单机任务管理器：文件持久化 + asyncio 后台执行。"""

    def __init__(self, config: VideoConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._running: dict[str, asyncio.Task] = {}

    # ── 内部工具 ──

    def _state_path(self, task_id: str) -> Path:
        return self._config.task_root / f"{task_id}.json"

    def _load(self, task_id: str) -> dict[str, Any] | None:
        path = self._state_path(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save(self, state: dict[str, Any]) -> None:
        self._config.task_root.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        path = self._state_path(state["task_id"])
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 任务提交 ──

    def submit(self, source: str, *, kind: str = "url", frames: int = 12) -> dict[str, Any]:
        """登记新任务并返回初始状态。调用方（router）负责实际启动后台执行。"""
        task_id = uuid.uuid4().hex[:16]
        state: dict[str, Any] = {
            "task_id": task_id,
            "source": source,
            "kind": kind,
            "status": STATUS_SUBMITTED,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "output_dir": None,
            "cache_manifest": None,
            "error": None,
            "stage": "submitted",
            "report": None,
            "transcript": None,
            "keyframes": [],
            "cost": None,
            "pid": None,
            "frames_requested": frames,
        }
        self._save(state)
        return state

    # ── 后台执行 ──

    async def run(self, task_id: str, source: str, *, frames: int = 12) -> None:
        """在事件循环中执行 quick_watch.py 并更新任务状态。"""
        state = self._load(task_id)
        if state is None:
            return
        state["status"] = STATUS_RUNNING
        state["stage"] = "starting"
        self._save(state)

        script = self._config.quick_watch_script
        if not script.exists():
            self._fail(task_id, f"quick-watch 脚本不存在: {script}")
            return

        # 输出目录：按任务 id 隔离，避免 quick-watch 默认 ~/quick-watch 目录混乱
        output_root = self._config.output_root / task_id
        output_root.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._config.python,
            str(script),
            source,
            "--frames", str(frames),
            "--output-root", str(output_root),
        ]
        # 继承父进程环境，覆盖 ASR 所需变量
        env = dict(__import__("os").environ)
        env["PYTHONIOENCODING"] = "utf-8"
        if self._config.dashscope_api_key:
            env["DASHSCOPE_API_KEY"] = self._config.dashscope_api_key

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(self._config.quick_watch_root),
            )
            state = self._load(task_id) or {}
            state["pid"] = proc.pid
            state["stage"] = "running"
            self._save(state)

            # 流式读取 stdout 解析进度（emit JSON lines）
            stdout_task = asyncio.create_task(self._drain_stdout(task_id, proc.stdout))
            stderr_task = asyncio.create_task(self._drain_stderr(task_id, proc.stderr))
            try:
                await asyncio.wait_for(proc.wait(), timeout=_TASK_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                self._fail(task_id, "任务超时（15 分钟）")
                return

            await stdout_task
            await stderr_task

            if proc.returncode != 0:
                stderr_text = stderr_task.result() or ""
                self._fail(task_id, f"quick_watch 退出码 {proc.returncode}: {stderr_text[-500:]}")
                return

            self._finalize(task_id)
        except FileNotFoundError:
            self._fail(task_id, f"找不到 Python 解释器: {self._config.python}")
        except Exception as exc:  # noqa: BLE001 — 任务失败统一落盘
            self._fail(task_id, f"任务执行异常: {exc}")

    async def _drain_stdout(self, task_id: str, stream: asyncio.StreamReader | None) -> None:
        """逐行读取 stdout；解析 emit 的 JSON lines 更新 stage。"""
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            # stdout 可能包含非 JSON 进度行（数字/普通文本）；只处理对象行
            if not isinstance(payload, dict):
                continue
            stage = payload.get("stage")
            if stage:
                state = self._load(task_id)
                if state is not None:
                    state["stage"] = stage
                    state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
                    self._save(state)

    async def _drain_stderr(self, task_id: str, stream: asyncio.StreamReader | None) -> str:
        """收集 stderr 文本（失败时用于诊断）。"""
        if stream is None:
            return ""
        chunks: list[str] = []
        while True:
            line = await stream.readline()
            if not line:
                break
            chunks.append(line.decode("utf-8", errors="replace").strip())
        return "\n".join(chunks)

    def _finalize(self, task_id: str) -> None:
        """quick_watch 成功退出后，从缓存 manifest 读取完整结果。

        quick-watch 将完整报告（report/transcript/keyframes/cost）写入
        ~/.cache/quick-watch/tasks/<fingerprint>/manifest.json，交付物目录
        manifest.json 只有任务元数据。本方法优先读缓存 manifest。
        """
        state = self._load(task_id)
        if state is None:
            return
        output_root = self._config.output_root / task_id
        state["output_dir"] = str(output_root)

        cache: dict[str, Any] | None = None
        # 1) 缓存 manifest（含完整报告）
        cache_dir = Path.home() / ".cache" / "quick-watch" / "tasks"
        if cache_dir.exists():
            # 按修改时间找最新的 manifest（本任务刚写入）
            candidates = sorted(
                cache_dir.glob("*/manifest.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for cand in candidates[:5]:
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                # 用 output_dir 或 source 关联到本任务
                out_dir = str(data.get("output_dir") or "")
                src = str(data.get("source") or "")
                if out_dir == str(output_root) or src == state.get("source"):
                    cache = data
                    state["cache_manifest"] = str(cand)
                    break
        # 2) 交付物 manifest.json（兜底：任务元数据）
        deliverable = output_root / "manifest.json"
        if cache is None and deliverable.exists():
            try:
                cache = json.loads(deliverable.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cache = {}

        if cache is not None:
            report = cache.get("report") if isinstance(cache.get("report"), dict) else {}
            state["report"] = report
            state["transcript"] = str(cache.get("transcript") or "")
            state["keyframes"] = report.get("keyframes") or []
            state["cost"] = report.get("cost") if isinstance(report.get("cost"), dict) else {}
        state["status"] = STATUS_COMPLETE
        state["stage"] = "complete"
        self._save(state)

    def _fail(self, task_id: str, message: str) -> None:
        state = self._load(task_id)
        if state is None:
            return
        state["status"] = STATUS_FAILED
        state["stage"] = "failed"
        state["error"] = message
        self._save(state)

    # ── 查询 ──

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._load(task_id)

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        task_root = self._config.task_root
        if not task_root.exists():
            return []
        states: list[dict[str, Any]] = []
        for path in sorted(task_root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            states.append(state)
            if len(states) >= limit:
                break
        return states

    # ── 启动 / 停止 ──

    def start_background(self, task_id: str, source: str, *, frames: int = 12) -> None:
        """启动后台任务（在事件循环内调度）。"""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self.run(task_id, source, frames=frames))
        self._running[task_id] = task
        task.add_done_callback(lambda _t: self._running.pop(task_id, None))

    def shutdown(self) -> None:
        """取消所有运行中任务（应用关闭时调用）。"""
        for task in list(self._running.values()):
            task.cancel()
        self._running.clear()
