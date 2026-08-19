"""视频解析 REST API。

端点：
  POST   /api/video/tasks             提交解析任务（body: {source, kind?, frames?}）
  POST   /api/video/tasks/upload      上传本地视频文件（multipart: file）
  GET    /api/video/tasks             任务列表
  GET    /api/video/tasks/{id}        任务状态与结果（含 summary）
  DELETE /api/video/tasks/{id}        删除任务（保留磁盘文件，仅清状态）
  GET    /api/video/tasks/{id}/frames/{name}  关键帧图片
  GET    /api/video/tasks/{id}/report.html    渲染后的 HTML 报告（缺失时实时渲染）
  POST   /api/video/tasks/{id}/qa     基于转录问答（视频 Chat 配置，回退系统 model_relay）
  GET    /api/video/settings          读取视频 agent 设置（密钥只给 has_*）
  PUT    /api/video/settings          更新设置（热更新 + DB 持久化）
  POST   /api/video/settings/test     测试连接（mode: asr | chat）
  GET    /api/video/env               运行环境信息
  GET    /api/video/library           历史任务库
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.api.dependencies import require_permission, require_user
from src.platform.principal import PERMISSION_SETTINGS_MANAGE, ProjectPrincipal
from src.shared.errors import AppError
from src.video import asr as video_asr
from src.video.service import STATUS_COMPLETE, VideoTaskManager
from src.video.settings import VideoAgentSettings
from src.video.summary import make_chat_client

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


class UpdateVideoSettingsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    # 空串 = 不更新（保留原值）
    asr_model: str | None = None
    asr_api_key: str | None = None
    asr_app_id: str | None = None
    asr_access_token: str | None = None
    # 空串 = 复用系统 model_relay
    chat_base_url: str | None = None
    chat_model: str | None = None
    chat_api_key: str | None = None
    frames: int | None = Field(default=None, ge=0, le=48)


class TestVideoSettingsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    mode: str = Field(pattern="^(asr|chat)$")
    asr_model: str | None = None
    asr_api_key: str | None = None
    asr_app_id: str | None = None
    asr_access_token: str | None = None
    chat_base_url: str | None = None
    chat_model: str | None = None
    chat_api_key: str | None = None


def _manager(request: Request) -> VideoTaskManager:
    manager = getattr(request.app.state, "video_manager", None)
    if manager is None:
        raise AppError("VIDEO_NOT_CONFIGURED", "视频解析服务未初始化", status_code=503)
    return manager


def _settings(request: Request) -> VideoAgentSettings:
    settings = getattr(request.app.state, "video_settings", None)
    if settings is None:
        raise AppError("VIDEO_NOT_CONFIGURED", "视频解析服务未初始化", status_code=503)
    return settings


def _safe_frame_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise AppError("VIDEO_BAD_REQUEST", "非法的帧文件名", status_code=400)
    return name


# ── 任务 ──

@router.post("/tasks")
async def submit_task(
    body: SubmitTaskRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """提交解析任务并立即开始后台执行。"""
    manager = _manager(request)
    source = body.source.strip()

    if body.kind == "file":
        p = Path(source).expanduser()
        if not p.exists():
            raise AppError("VIDEO_FILE_NOT_FOUND", "本地视频文件不存在", status_code=404)
    else:
        if not (source.startswith("http://") or source.startswith("https://")):
            raise AppError("VIDEO_BAD_URL", "仅支持 http/https 视频链接", status_code=400)

    state = manager.submit(source, kind=body.kind, frames=body.frames)
    manager.start_background(state["task_id"], source, frames=body.frames, kind=body.kind)
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
    """视频解析运行环境信息（agent设置页展示）。"""
    manager = _manager(request)
    settings = _settings(request)
    cfg = manager._config
    return {
        "success": True,
        "data": {
            "pipeline": "builtin",
            "python": cfg.python,
            "library_root": str(cfg.library_root),
            "output_root": str(cfg.output_root),
            "task_root": str(cfg.task_root),
            "upload_dir": str(cfg.upload_dir),
            "asr_configured": settings.asr_configured,
        },
    }


@router.get("/settings")
async def get_video_settings(
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_SETTINGS_MANAGE)),
) -> dict[str, object]:
    """读取视频 agent 设置（密钥不回显，只暴露 has_*）。"""
    settings = _settings(request)
    return {"success": True, "data": settings.to_dict()}


@router.put("/settings")
async def update_video_settings(
    body: UpdateVideoSettingsRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_SETTINGS_MANAGE)),
) -> dict[str, object]:
    """更新视频 agent 设置：热更新立即生效 + DB 持久化。

    更新语义：
    - asr_model / chat_*：提供且非空 = 更新；空串 = 特殊语义（chat_* 空 = 复用系统）。
    - asr_api_key / asr_app_id / asr_access_token：空串 = 不更新（保留原值）。
    """
    settings = _settings(request)
    app = request.app

    changed = False
    if body.asr_model is not None and body.asr_model.strip() and body.asr_model != settings.asr_model:
        settings.asr_model = body.asr_model.strip()
        changed = True
    if body.asr_api_key is not None and body.asr_api_key != "" and body.asr_api_key != settings.asr_api_key:
        settings.asr_api_key = body.asr_api_key
        changed = True
    if body.asr_app_id is not None and body.asr_app_id != "" and body.asr_app_id != settings.asr_app_id:
        settings.asr_app_id = body.asr_app_id
        changed = True
    if body.asr_access_token is not None and body.asr_access_token != "" and body.asr_access_token != settings.asr_access_token:
        settings.asr_access_token = body.asr_access_token
        changed = True
    # chat_*：缺省（未传）不更新；空串 = 复用系统；非空 = 独立配置
    if body.chat_base_url is not None and body.chat_base_url != settings.chat_base_url:
        settings.chat_base_url = body.chat_base_url.strip()
        changed = True
    if body.chat_model is not None and body.chat_model != settings.chat_model:
        settings.chat_model = body.chat_model.strip()
        changed = True
    if body.chat_api_key is not None and body.chat_api_key != "" and body.chat_api_key != settings.chat_api_key:
        settings.chat_api_key = body.chat_api_key
        changed = True
    if body.frames is not None and body.frames != settings.frames:
        settings.frames = body.frames
        changed = True

    if not changed:
        return {"success": True, "data": settings.to_dict()}

    # 持久化到 DB
    db = app.state.session_factory()
    try:
        await app.state.video_settings_service.save(db)
    finally:
        await db.close()

    return {"success": True, "data": settings.to_dict()}


@router.post("/settings/test")
async def test_video_settings(
    body: TestVideoSettingsRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_SETTINGS_MANAGE)),
) -> dict[str, object]:
    """测试连接：asr 用火山 flash 极速识别探测；chat 试调一次 chat/completions。

    失败返回 200 包裹的 {ok: False, message}（不抛 500）。
    """
    current = _settings(request)
    app = request.app

    if body.mode == "asr":
        candidate = current.copy()
        if body.asr_model is not None and body.asr_model.strip():
            candidate.asr_model = body.asr_model.strip()
        if body.asr_api_key is not None and body.asr_api_key != "":
            candidate.asr_api_key = body.asr_api_key
        if body.asr_app_id is not None and body.asr_app_id != "":
            candidate.asr_app_id = body.asr_app_id
        if body.asr_access_token is not None and body.asr_access_token != "":
            candidate.asr_access_token = body.asr_access_token
        ok, message = await asyncio.to_thread(video_asr.test_asr_connection, candidate, 30.0)
        return {"success": True, "data": {"ok": ok, "message": message}}

    # chat 模式
    candidate = current.copy()
    if body.chat_base_url is not None:
        candidate.chat_base_url = body.chat_base_url.strip()
    if body.chat_model is not None:
        candidate.chat_model = body.chat_model.strip()
    if body.chat_api_key is not None and body.chat_api_key != "":
        candidate.chat_api_key = body.chat_api_key
    try:
        chat_client, owns = make_chat_client(candidate, app)
        try:
            messages = [{"role": "user", "content": "hi"}]
            async for _ in chat_client.stream(messages):
                break
        finally:
            if owns:
                await chat_client._client.aclose()
        return {"success": True, "data": {"ok": True}}
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc) or "连接失败"
        return {"success": True, "data": {"ok": False, "message": message}}


@router.get("/library")
async def list_library(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """视频数据库：扫描本机历史解析任务（C 盘 quick-watch 输出目录）。"""
    manager = _manager(request)
    tasks = manager.list_library()
    return {"success": True, "data": tasks}


@router.delete("/library/{task_dir}")
async def delete_library_task(
    task_dir: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """删除历史视频库中的单个任务（目录会被永久删除，不可恢复）。"""
    manager = _manager(request)
    try:
        removed = manager.delete_library_task(task_dir)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError("VIDEO_LIBRARY_DELETE_FAILED", f"删除任务失败: {exc}", status_code=400) from exc
    return {"success": True, "data": {"deleted": str(removed)}}


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
    manager = _manager(request)
    safe = _safe_frame_name(name)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_dir):
        raise AppError("VIDEO_BAD_REQUEST", "非法的任务目录名", status_code=400)
    frame_path = manager._config.library_root / task_dir / "frames" / safe
    if not frame_path.exists():
        raise AppError("VIDEO_FRAME_NOT_FOUND", "帧图片不存在", status_code=404)
    return FileResponse(str(frame_path), media_type="image/jpeg")


@router.get("/library/{task_dir}/report.html")
async def get_library_report(
    task_dir: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
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
    """返回渲染后的 HTML 报告；文件缺失时用 summary/transcript 实时渲染。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    output_dir = state.get("output_dir")
    if not output_dir:
        raise AppError("VIDEO_NO_REPORT", "任务尚未生成报告", status_code=404)
    html_path = Path(output_dir) / "report.html"
    if not html_path.exists():
        # 实时渲染：用 summary.json / 状态中的转录与关键帧
        from src.video.report import render_report_html

        transcript = str(state.get("transcript") or "")
        keyframes = state.get("keyframes") or []
        summary = state.get("summary") or {}
        report = state.get("report") or {}
        if not transcript and not keyframes:
            raise AppError("VIDEO_NO_REPORT", "该任务没有可渲染的报告内容", status_code=404)
        try:
            render_report_html(
                output_path=html_path,
                report=report,
                transcript=transcript,
                keyframes=keyframes,
                summary=summary if isinstance(summary, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            raise AppError("VIDEO_NO_REPORT", f"报告渲染失败: {exc}", status_code=404) from exc
    return FileResponse(str(html_path), media_type="text/html; charset=utf-8")


@router.post("/tasks/{task_id}/qa")
async def ask_question(
    task_id: str,
    body: QaRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """基于转录全文回答用户问题（视频 Chat 配置，回退系统 model_relay）。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    if state.get("status") != STATUS_COMPLETE:
        raise AppError("VIDEO_NOT_READY", "任务尚未完成，暂不能问答", status_code=409)
    transcript = state.get("transcript") or ""
    if not transcript.strip():
        raise AppError("VIDEO_NO_TRANSCRIPT", "该任务没有可用转录", status_code=422)

    settings = _settings(request)
    chat_client, owns = make_chat_client(settings, request.app)
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
    finally:
        if owns:
            await chat_client._client.aclose()
    return {"success": True, "data": {"answer": answer}}
