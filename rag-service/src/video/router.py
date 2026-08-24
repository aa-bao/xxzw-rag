"""视频解析 REST API。

端点：
  POST   /api/video/tasks             提交解析任务（body: {source, kind?, frames?}）
  POST   /api/video/tasks/upload      上传本地视频文件（multipart: file）
  GET    /api/video/tasks             任务列表
  GET    /api/video/tasks/{id}        任务状态与结果（含 summary）
  DELETE /api/video/tasks/{id}        删除任务（保留磁盘文件，仅清状态）
  GET    /api/video/tasks/{id}/frames/{name}  关键帧图片
  GET    /api/video/tasks/{id}/video          可播放视频（低码率副本/本地上传）
  GET    /api/video/tasks/{id}/audio          音频产物
  GET    /api/video/tasks/{id}/report.html    渲染后的 HTML 报告（缺失时实时渲染）
  POST   /api/video/tasks/{id}/qa     基于转录问答（视频 Chat 配置，回退系统 model_relay）
  GET    /api/video/settings          读取视频 agent 设置（密钥只给 has_*）
  PUT    /api/video/settings          更新设置（热更新 + DB 持久化）
  POST   /api/video/settings/test     测试连接（mode: asr | chat | chat_summary | chat_qa）
  GET    /api/video/env               运行环境信息
  GET    /api/video/library           历史任务库
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.api.dependencies import require_permission, require_user
from src.db.repositories import VideoTaskRepository
from src.platform.principal import PERMISSION_SETTINGS_MANAGE, ProjectPrincipal
from src.shared.errors import AppError
from src.video import asr as video_asr
from src.video.service import STATUS_COMPLETE, VideoTaskManager
from src.video.settings import VideoAgentSettings
from src.video.summary import make_chat_client

router = APIRouter(prefix="/api/video", tags=["video"])

_MAX_UPLOAD_MB = 500
# Agent 头像：随后端静态资源发布，不依赖本机任意图片目录
AGENT_AVATAR_PATH = Path(__file__).resolve().parent.parent.parent / "static" / "nl-che.jpg"
_ALLOWED_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv", ".mp3", ".m4a", ".aac", ".wav"}


class SubmitTaskRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="url", pattern="^(url|file)$")
    frames: int = Field(default=12, ge=0, le=48)
    content_type: str = Field(default="auto", pattern="^(auto|video|image_text)$")


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
    qa_model: str | None = None
    qa_base_url: str | None = None
    qa_api_key: str | None = None
    frames: int | None = Field(default=None, ge=0, le=48)


class TestVideoSettingsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    mode: str = Field(pattern="^(asr|chat|chat_summary|chat_qa)$")
    asr_model: str | None = None
    asr_api_key: str | None = None
    asr_app_id: str | None = None
    asr_access_token: str | None = None
    chat_base_url: str | None = None
    chat_model: str | None = None
    chat_api_key: str | None = None
    qa_model: str | None = None
    qa_base_url: str | None = None
    qa_api_key: str | None = None


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


def _extract_url_from_share_text(text: str) -> str:
    """从分享文案中提取第一个 http(s) 链接；没有链接时原样返回。"""
    value = (text or "").strip()
    match = re.search(r"https?://[^\s]+", value)
    if not match:
        return value
    return re.sub(r"[),.;!?，。；：！？、）》】\"'”’]+$", "", match.group(0))


def _task_media_path(state: dict, key: str, output_suffixes: tuple[str, ...]) -> Path | None:
    """在任务状态/输出目录中定位视频或音频文件（兼容历史任务）。"""
    raw = state.get(key)
    if raw:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute() and state.get("output_dir"):
            p = Path(str(state["output_dir"])) / p
        if p.is_file():
            return p
    output_dir = state.get("output_dir")
    if output_dir:
        output = Path(str(output_dir))
        for suffix in output_suffixes:
            for p in output.glob(f"*{suffix}"):
                if p.is_file():
                    return p
    return None


# ── QA 上下文构建 ──

def _qa_context_text(state: dict) -> str:
    """把转录 + 摘要/画面说明拼成问答上下文。"""
    transcript = str(state.get("transcript") or "")
    summary = state.get("summary") or {}
    if not isinstance(summary, dict):
        summary = {}

    parts: list[str] = []
    if transcript.strip():
        parts.append("【完整转录（带时间戳）】\n" + transcript[:_TRANSCRIPT_LIMIT_QA])
    # 图文帖：正文/作者/标签/发布时间
    if state.get("post_text"):
        parts.append("【图文正文】\n" + str(state["post_text"])[:_TRANSCRIPT_LIMIT_QA])
    if state.get("author"):
        parts.append(f"【作者】\n{state['author']}")
    hashtags = state.get("hashtags") or []
    if isinstance(hashtags, list) and hashtags:
        parts.append("【话题标签】\n" + " ".join(f"#{h}" for h in hashtags))
    if state.get("publish_time"):
        parts.append(f"【发布时间】\n{state['publish_time']}")
    if summary.get("summary"):
        parts.append("【摘要】\n" + str(summary["summary"]))
    keypoints = summary.get("keypoints") or []
    if keypoints:
        parts.append("【要点】\n" + "\n".join(f"- {k}" for k in keypoints))
    visual_notes = summary.get("visual_notes") or []
    if visual_notes:
        parts.append("【画面洞察】\n" + "\n".join(f"- {v}" for v in visual_notes))
    captions = summary.get("keyframe_captions") or {}
    if isinstance(captions, dict) and captions:
        parts.append(
            "【关键帧画面说明】\n"
            + "\n".join(f"- [{ts}] {cap}" for ts, cap in captions.items())
        )
    image_captions = summary.get("image_captions") or {}
    if isinstance(image_captions, dict) and image_captions:
        parts.append(
            "【帖子图片说明】\n"
            + "\n".join(f"- {key}: {cap}" for key, cap in image_captions.items())
        )
    return "\n\n".join(parts)


def _qa_system_prompt() -> str:
    return (
        "你是内容解析助手。以下是某个视频/图文帖的真实内容：转录（带 [MM:SS] 时间戳）、"
        "摘要要点、图文正文以及可用的画面/图片说明。请只依据这些内容回答用户的问题："
        "回答中引用时间戳 [MM:SS] 或图片序号说明出处；区分「文字提到」和「画面/图片中看到」；"
        "内容中没有依据时明确说明，不要编造。"
        "回答要简洁：普通问题控制在 300 字以内，需要分点时才使用短列表，不要展开无关内容。"
    )


def _build_qa_messages(
    context: str,
    history: list[dict],
    question: str,
    *,
    with_images: bool = False,
    keyframes: list[dict] | None = None,
    images: list[dict] | None = None,
) -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": _qa_system_prompt() + "\n\n" + context}
    ]
    # 历史对话（纯文本）
    for item in history[-12:]:
        role = "user" if item.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": str(item.get("content") or "")})
    # 当前问题：必要时带上图片（关键帧 或 图文帖子原图）
    if with_images:
        content: list[dict] = [{"type": "text", "text": question}]
        image_items = images or []
        if not image_items:
            image_items = [{**kf, "path": str(kf.get("path") or "")} for kf in (keyframes or [])[:4]]
        for img in image_items[:8]:
            path = str(img.get("path") or "")
            if not path:
                continue
            import base64
            from pathlib import Path as _P

            p = _P(path)
            if not p.is_file():
                continue
            try:
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            except OSError:
                continue
            mime = "image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": question})
    return messages


_TRANSCRIPT_LIMIT_QA = 12000


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
    if body.kind == "url":
        # 分享文案通常夹带中文说明，自动提取其中的 http(s) 链接
        source = _extract_url_from_share_text(source)

    if body.kind == "file":
        p = Path(source).expanduser()
        if not p.exists():
            raise AppError("VIDEO_FILE_NOT_FOUND", "本地视频文件不存在", status_code=404)
    else:
        if not (source.startswith("http://") or source.startswith("https://")):
            raise AppError("VIDEO_BAD_URL", "仅支持 http/https 视频链接", status_code=400)

    state = manager.submit(
        source, kind=body.kind, frames=body.frames, content_type=body.content_type
    )
    await manager.persist_to_db(state)
    manager.start_background(
        state["task_id"], source, frames=body.frames, kind=body.kind,
        content_type=body.content_type,
    )
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


@router.get("/agent-avatar")
async def get_agent_avatar(
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """返回视频 Agent 头像（后端静态资源，不暴露本机图片路径）。"""
    if not AGENT_AVATAR_PATH.is_file():
        raise AppError("VIDEO_AVATAR_NOT_FOUND", "Agent 头像资源不存在", status_code=404)
    return FileResponse(str(AGENT_AVATAR_PATH), media_type="image/jpeg")


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
    # qa_model：空串 = 跟随 chat_model（摘要模型）
    if body.qa_model is not None and body.qa_model != settings.qa_model:
        settings.qa_model = body.qa_model.strip()
        changed = True
    # qa_base_url：空串 = 复用摘要模型 Base URL
    if body.qa_base_url is not None and body.qa_base_url != settings.qa_base_url:
        settings.qa_base_url = body.qa_base_url.strip()
        changed = True
    # qa_api_key：空串 = 不更新（保留原值；留空即继续复用已配置 key）
    if body.qa_api_key is not None and body.qa_api_key != "" and body.qa_api_key != settings.qa_api_key:
        settings.qa_api_key = body.qa_api_key
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

    # chat 模式：chat / chat_summary 测试摘要模型；chat_qa 测试问答模型
    candidate = current.copy()
    if body.chat_base_url is not None:
        candidate.chat_base_url = body.chat_base_url.strip()
    if body.chat_model is not None:
        candidate.chat_model = body.chat_model.strip()
    if body.chat_api_key is not None and body.chat_api_key != "":
        candidate.chat_api_key = body.chat_api_key
    if body.qa_base_url is not None:
        candidate.qa_base_url = body.qa_base_url.strip()
    if body.qa_api_key is not None and body.qa_api_key != "":
        candidate.qa_api_key = body.qa_api_key

    if body.mode == "chat_qa":
        model = body.qa_model or current.qa_model or current.chat_model
        if body.qa_model is not None:
            candidate.qa_model = body.qa_model.strip()
    else:
        model = body.chat_model or current.chat_model

    try:
        chat_client, owns = make_chat_client(
            candidate,
            app,
            model=model,
            qa=(body.mode == "chat_qa"),
        )
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
    seen = {str(t.get("task_id")) for t in tasks}
    # 文件状态缺失的任务（如已删除状态文件但 MySQL 仍有存档）从 DB 补回
    try:
        async with request.app.state.session_factory() as session:
            records = await VideoTaskRepository(session).list(limit=200)
        for record in records:
            if record.task_id not in seen:
                tasks.append(VideoTaskRepository.record_to_state(record))
                seen.add(record.task_id)
    except Exception:
        # DB 不可用时保持纯文件列表
        pass
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
        # 文件状态缺失时从 MySQL 归档读取
        try:
            async with request.app.state.session_factory() as session:
                record = await VideoTaskRepository(session).get(task_id)
            if record is not None:
                state = VideoTaskRepository.record_to_state(record)
        except Exception:
            pass
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    return {"success": True, "data": state}


@router.get("/tasks/{task_id}/events")
async def get_task_events(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
):
    """SSE 事件流：实时推送视频解析流水线的每一步。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    return StreamingResponse(
        manager.event_stream(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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
    manager._close_event_stream(task_id)
    await manager.delete_from_db(task_id)
    return {"success": True, "data": None}


def _library_task_path(manager: VideoTaskManager, task_dir: str, relative: str) -> Path:
    """在旧库（library_root）与新输出目录（output_root）中定位任务文件。"""
    for root in (manager._config.library_root, manager._config.output_root):
        candidate = root / task_dir / relative
        if candidate.exists():
            return candidate
    return root / task_dir / relative


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
    frame_path = _library_task_path(manager, task_dir, f"frames/{safe}")
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
    report_path = _library_task_path(manager, task_dir, "report.html")
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
    frame_path: Path | None = None
    output_dir = state.get("output_dir")
    if output_dir:
        candidate = Path(output_dir) / "frames" / safe
        if candidate.exists():
            frame_path = candidate
    if frame_path is None:
        for kf in state.get("keyframes") or []:
            p = Path(kf.get("path", ""))
            if p.name == safe and p.exists():
                frame_path = p
                break
    if frame_path is None:
        raise AppError("VIDEO_FRAME_NOT_FOUND", "帧图片不存在", status_code=404)
    return FileResponse(str(frame_path), media_type="image/jpeg")


@router.get("/tasks/{task_id}/images/{name}")
async def get_post_image(
    task_id: str,
    name: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """返回图文任务的帖子原图。"""
    manager = _manager(request)
    safe = _safe_frame_name(name)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    image_path: Path | None = None
    output_dir = state.get("output_dir")
    if output_dir:
        candidate = Path(output_dir) / "images" / safe
        if candidate.exists():
            image_path = candidate
    if image_path is None:
        for img in state.get("post_images") or []:
            p = Path(str(img.get("path") or ""))
            if p.name == safe and p.exists():
                image_path = p
                break
    if image_path is None:
        raise AppError("VIDEO_IMAGE_NOT_FOUND", "图片不存在", status_code=404)
    return FileResponse(str(image_path), media_type="image/jpeg")


@router.get("/tasks/{task_id}/video")
async def get_task_video(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """返回任务可播放视频（低码率持久化副本 / 本地上传文件）。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    video_path = _task_media_path(state, "video_path", (".mp4", ".webm", ".mov", ".mkv", ".m4v"))
    if video_path is None and state.get("kind") == "file":
        # 兼容历史本地文件任务：源文件仍然存在时可直接播放
        candidate = Path(str(state.get("source") or "")).expanduser()
        if candidate.is_file() and candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}:
            video_path = candidate
    if video_path is None:
        raise AppError("VIDEO_FILE_NOT_FOUND", "任务还没有可播放的视频", status_code=404)
    is_download = request.query_params.get("download") == "1"
    return FileResponse(
        str(video_path),
        media_type=mimetypes.guess_type(video_path.name)[0] or "application/octet-stream",
        filename=video_path.name,
        content_disposition_type="attachment" if is_download else "inline",
    )


@router.get("/tasks/{task_id}/audio")
async def get_task_audio(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> FileResponse:
    """返回任务音频产物（mp3），供右侧媒体面板预览/下载。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    audio_path = _task_media_path(state, "audio_path", (".mp3", ".m4a", ".aac"))
    if audio_path is None:
        raise AppError("VIDEO_AUDIO_NOT_FOUND", "任务还没有音频产物", status_code=404)
    is_download = request.query_params.get("download") == "1"
    return FileResponse(
        str(audio_path),
        media_type="audio/mpeg",
        filename=audio_path.name,
        content_disposition_type="attachment" if is_download else "inline",
    )


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


@router.get("/tasks/{task_id}/qa/history")
async def get_qa_history(
    task_id: str,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """读取某任务的问答历史（多轮追问用）。"""
    manager = _manager(request)
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    return {"success": True, "data": manager.get_qa_history(task_id)}


def _require_complete_task(manager, task_id: str) -> dict:
    state = manager.get(task_id)
    if state is None:
        raise AppError("VIDEO_TASK_NOT_FOUND", "任务不存在", status_code=404)
    if state.get("status") != STATUS_COMPLETE:
        raise AppError("VIDEO_NOT_READY", "任务尚未完成，暂不能问答", status_code=409)
    transcript = state.get("transcript") or ""
    if not transcript.strip() and state.get("content_type") != "image_text":
        raise AppError("VIDEO_NO_TRANSCRIPT", "该任务没有可用转录", status_code=422)
    return state


@router.post("/tasks/{task_id}/qa")
async def ask_question(
    task_id: str,
    body: QaRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
) -> dict[str, object]:
    """基于视频真实内容（转录 + 摘要 + 画面说明）回答用户问题。"""
    manager = _manager(request)
    state = _require_complete_task(manager, task_id)
    settings = _settings(request)
    history = manager.get_qa_history(task_id)
    context = _qa_context_text(state)
    keyframes = state.get("keyframes") or []
    post_images = state.get("post_images") or []
    is_image_text = state.get("content_type") == "image_text"
    with_images = bool(post_images) if is_image_text else ((settings.qa_configured or settings.chat_configured) and bool(keyframes))
    messages = _build_qa_messages(
        context,
        history,
        body.question,
        with_images=with_images,
        keyframes=keyframes,
        images=post_images if is_image_text else None,
    )

    chat_client, owns = make_chat_client(
        settings,
        request.app,
        model=settings.qa_model or settings.chat_model,
        qa=True,
    )
    try:
        try:
            answer = await chat_client.complete(messages, max_tokens=1500)
        except Exception:
            if not with_images:
                raise
            # 多模态通道不可用时降级为纯转录/画面说明文本
            messages_text = _build_qa_messages(
                context, history, body.question, with_images=False
            )
            answer = await chat_client.complete(messages_text, max_tokens=1500)
    except Exception as exc:  # noqa: BLE001
        raise AppError("VIDEO_QA_FAILED", f"问答服务异常: {exc}", status_code=502) from exc
    finally:
        if owns:
            await chat_client._client.aclose()
    manager.append_qa_message(task_id, "user", body.question)
    manager.append_qa_message(task_id, "assistant", answer)
    await manager.persist_to_db(task_id)
    return {"success": True, "data": {"answer": answer}}


@router.post("/tasks/{task_id}/qa/stream")
async def ask_question_stream(
    task_id: str,
    body: QaRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_user),
):
    """SSE 流式问答：边生成边返回，并把多轮对话保存到任务状态。"""
    manager = _manager(request)
    state = _require_complete_task(manager, task_id)
    settings = _settings(request)
    history = manager.get_qa_history(task_id)
    context = _qa_context_text(state)
    keyframes = state.get("keyframes") or []
    post_images = state.get("post_images") or []
    is_image_text = state.get("content_type") == "image_text"
    with_images = bool(post_images) if is_image_text else ((settings.qa_configured or settings.chat_configured) and bool(keyframes))
    messages = _build_qa_messages(
        context,
        history,
        body.question,
        with_images=with_images,
        keyframes=keyframes,
        images=post_images if is_image_text else None,
    )
    manager.append_qa_message(task_id, "user", body.question)

    chat_client, owns = make_chat_client(
        settings,
        request.app,
        model=settings.qa_model or settings.chat_model,
        qa=True,
    )

    async def sse_stream():
        try:
            yield VideoTaskManager._sse_event("start", {"question": body.question})
            chunks: list[str] = []
            try:
                async for chunk in chat_client.stream(messages, max_tokens=1500):
                    chunks.append(chunk)
                    yield VideoTaskManager._sse_event("chunk", {"content": chunk})
            except Exception:
                if not with_images or chunks:
                    raise
                # 多模态通道不可用时降级为纯转录/画面说明文本
                messages_text = _build_qa_messages(
                    context, history, body.question, with_images=False
                )
                async for chunk in chat_client.stream(messages_text, max_tokens=1500):
                    chunks.append(chunk)
                    yield VideoTaskManager._sse_event("chunk", {"content": chunk})
            answer = "".join(chunks).strip()
            if not answer:
                raise RuntimeError("模型未返回内容")
            manager.append_qa_message(task_id, "assistant", answer)
            await manager.persist_to_db(task_id)
            yield VideoTaskManager._sse_event("done", {"answer": answer})
        except Exception as exc:  # noqa: BLE001
            yield VideoTaskManager._sse_event(
                "error", {"message": f"问答服务异常: {exc}"}
            )
        finally:
            if owns:
                try:
                    await chat_client._client.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
