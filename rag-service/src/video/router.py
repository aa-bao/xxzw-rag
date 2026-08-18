"""视频解析 REST API。

端点：
  POST   /api/video/tasks         提交解析任务（body: {source, kind?, frames?}）
  POST   /api/video/tasks/upload  上传本地视频文件（multipart: file）
  GET    /api/video/tasks         任务列表
  GET    /api/video/tasks/{id}    任务状态与结果
  DELETE /api/video/tasks/{id}    删除任务（保留磁盘文件，仅清状态）
  GET    /api/video/tasks/{id}/frames/{name}  关键帧图片
  GET    /api/video/tasks/{id}/report.html    渲染后的 HTML 报告（若已生成）
  POST   /api/video/tasks/{id}/qa 基于转录问答（复用 model_relay）
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.api.dependencies import require_user
from src.models.llm import ChatClient
from src.platform.principal import ProjectPrincipal
from src.shared.errors import AppError
from src.video.service import STATUS_COMPLETE, STATUS_FAILED, VideoTaskManager

router = APIRouter(prefix="/api/video", tags=["video"])

_MAX_UPLOAD_MB = 500
_ALLOWED_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv", ".mp3", ".m4a", ".aac", ".wav"}


class SubmitTaskRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="url", pattern="^(url|file)$")
    frames: int = Field(default=12, ge=0, le=48)


class QaRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question: str = Field(min_length=1, max_length=2000)


def _manager(request: Request) -> VideoTaskManager:
    manager = getattr(request.app.state, "video_manager", None)
    if manager is None:
        raise AppError("VIDEO_NOT_CONFIGURED", "视频解析服务未初始化", status_code=503)
    return manager


def _safe_frame_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise AppError("VIDEO_BAD_REQUEST", "非法的帧文件名", status_code=400)
    return name


@router.post("/tasks")
async def submit_task(
    body: SubmitTaskRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """提交 URL 解析任务并立即开始后台执行。"""
    manager = _manager(request)
    source = body.source.strip()

    if body.kind == "file":
        # 前端先上传拿路径，再提交（见 upload 端点）
        p = Path(source).expanduser()
        if not p.exists():
            raise AppError("VIDEO_FILE_NOT_FOUND", "本地视频文件不存在", status_code=404)
    else:
        # 简单 URL 校验：http/https
        if not (source.startswith("http://") or source.startswith("https://")):
            raise AppError("VIDEO_BAD_URL", "仅支持 http/https 视频链接", status_code=400)

    state = manager.submit(source, kind=body.kind, frames=body.frames)
    manager.start_background(state["task_id"], source, frames=body.frames)
    return {"success": True, "data": state}


@router.post("/tasks/upload")
async def upload_video(
    request: Request,
    file: UploadFile,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """上传本地视频文件，返回暂存路径（供 submit 使用）。"""
    manager = _manager(request)
    filename = file.filename or "video"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_VIDEO_EXTS:
        raise AppError("VIDEO_BAD_TYPE", f"不支持的文件类型: {ext or '(无扩展名)'}", status_code=400)

    upload_dir = manager._config.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    # 防路径穿越：仅用安全文件名
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    dest = upload_dir / f"{principal.internal_user_id}_{safe_name}"

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_MB * 1024 * 1024:
                    dest.unlink(missing_ok=True)
                    raise AppError("VIDEO_TOO_LARGE", f"文件超过 {_MAX_UPLOAD_MB}MB 限制", status_code=413)
                out.write(chunk)
    except AppError:
        raise
    except OSError as exc:
        raise AppError("VIDEO_UPLOAD_FAILED", f"文件保存失败: {exc}", status_code=500) from exc

    return {"success": True, "data": {"path": str(dest), "size": size, "filename": filename}}


@router.get("/env")
async def get_env(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """视频解析运行环境信息（系统设置页展示）。"""
    manager = _manager(request)
    cfg = manager._config
    return {
        "success": True,
        "data": {
            "script": str(cfg.quick_watch_script),
            "python": cfg.python,
            "library_root": str(cfg.library_root),
            "output_root": str(cfg.output_root),
            "task_root": str(cfg.task_root),
            "upload_dir": str(cfg.upload_dir),
            "dashscope_ready": bool(cfg.dashscope_api_key),
        },
    }


@router.get("/prefs")
async def get_prefs(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """读取解析偏好（localStorage 由前端管理，这里返回默认值占位）。"""
    return {"success": True, "data": {"frames": 12, "qa_model": ""}}


@router.put("/prefs")
async def put_prefs(
    body: dict[str, object],
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """保存解析偏好（当前为占位实现，实际偏好存前端 localStorage）。"""
    return {"success": True, "data": {"saved": True}}


@router.get("/library")
async def list_library(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """视频数据库：扫描本机历史解析任务（C 盘 quick-watch 输出目录）。"""
    manager = _manager(request)
    tasks = manager.list_library()
    return {"success": True, "data": tasks}


@router.get("/tasks")
async def list_tasks(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    manager = _manager(request)
    tasks = manager.list_tasks(limit=100)
    return {"success": True, "data": tasks}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    return {"success": True, "data": state}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    # 取消运行中的任务
    running = manager._running.get(task_id)
    if running is not None:
        running.cancel()
    path = manager._state_path(task_id)
    path.unlink(missing_ok=True)
    return {"success": True, "data": None}


@router.get("/library/{task_dir}/frames/{name}")
async def get_library_frame(
    task_dir: str,
    name: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """视频数据库：返回历史任务目录内的关键帧图片。"""
    manager = _manager(request)
    safe = _safe_frame_name(name)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_dir):
        raise AppError("VIDEO_BAD_REQUEST", "非法的任务目录名", status_code=400)
    library_root = manager._config.library_root
    frame_path = library_root / task_dir / "frames" / safe
    if not frame_path.exists():
        raise AppError("VIDEO_FRAME_NOT_FOUND", "帧图片不存在", status_code=404)
    return FileResponse(str(frame_path), media_type="image/jpeg")


@router.get("/library/{task_dir}/report.html")
async def get_library_report(
    task_dir: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """视频数据库：返回历史任务的 HTML 报告。"""
    manager = _manager(request)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_dir):
        raise AppError("VIDEO_BAD_REQUEST", "非法的任务目录名", status_code=400)
    report_path = manager._config.library_root / task_dir / "report.html"
    if not report_path.exists():
        raise AppError("VIDEO_NO_REPORT", "该任务没有 HTML 报告", status_code=404)
    return FileResponse(str(report_path), media_type="text/html; charset=utf-8")


@router.get("/tasks/{task_id}/frames/{name}")
async def get_frame(
    task_id: str,
    name: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """返回关键帧图片（JPEG）。"""
    manager = _manager(request)
    safe = _safe_frame_name(name)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    output_dir = state.get("output_dir")
    if not output_dir:
        raise AppError("VIDEO_NO_FRAMES", "任务尚未生成关键帧", status_code=404)
    frame_path = Path(output_dir) / "frames" / safe
    if not frame_path.exists():
        # 兼容 keyframes 记录里的绝对路径
        for kf in state.get("keyframes") or []:
            p = Path(kf.get("path", ""))
            if p.name == safe and p.exists():
                frame_path = p
                break
        else:
            raise AppError("VIDEO_FRAME_NOT_FOUND", "帧图片不存在", status_code=404)
    return FileResponse(str(frame_path), media_type="image/jpeg")


@router.get("/tasks/{task_id}/report.html")
async def get_report_html(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """返回渲染后的 HTML 报告（若 quick-watch 已生成 report.html）。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    output_dir = state.get("output_dir")
    if not output_dir:
        raise AppError("VIDEO_NO_REPORT", "任务尚未生成报告", status_code=404)
    html_path = Path(output_dir) / "report.html"
    if not html_path.exists():
        raise AppError("VIDEO_NO_REPORT", "HTML 报告不存在（可尝试用 summary 渲染）", status_code=404)
    return FileResponse(str(html_path), media_type="text/html; charset=utf-8")


@router.post("/tasks/{task_id}/qa")
async def ask_question(
    task_id: str,
    body: QaRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """基于转录全文回答用户问题（复用 model_relay 的 ChatClient）。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    if state.get("status") != STATUS_COMPLETE:
        raise AppError("VIDEO_NOT_READY", "任务尚未完成，暂不能问答", status_code=409)
    transcript = state.get("transcript") or ""
    if not transcript.strip():
        raise AppError("VIDEO_NO_TRANSCRIPT", "该任务没有可用转录", status_code=422)

    chat_client = ChatClient(request.app.state.settings_shell, request.app.state.model_relay_client._client)
    messages = [
        {
            "role": "system",
            "content": (
                "你是视频解析助手。以下是视频的完整转录文本（带时间戳）。"
                "请基于转录内容回答用户问题，引用时间戳 [MM:SS] 说明出处；"
                "转录中没有依据时明确说明。\n\n"
                "【视频转录】\n" + transcript[:12000]
            ),
        },
        {"role": "user", "content": body.question},
    ]
    try:
        answer = await chat_client.complete(messages)
    except Exception as exc:  # noqa: BLE001
        raise AppError("VIDEO_QA_FAILED", f"问答服务异常: {exc}", status_code=502) from exc
    return {"success": True, "data": {"answer": answer}}
