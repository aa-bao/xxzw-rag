"""视频解析任务服务：纯 asyncio 原生流水线（流水线内建，无外部脚本依赖）。

任务生命周期（文件系统状态机）：
  submitted → running → complete | failed

状态文件：<task_root>/<task_id>.json
  {
    "task_id", "source", "kind", "status", "created_at", "updated_at",
    "output_dir", "cache_manifest"(None), "error", "stage", "report",
    "transcript", "keyframes", "cost", "frames_requested", "transcript_source",
    "summary", "pid"(None)
  }

流水线：下载/字幕 → 音频 → 静音检测/分片 → ASR 转写（并发，与关键帧并行）
      → 摘要（Chat 模型）→ HTML 报告。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from src.db.repositories import VideoTaskRepository
from src.video import acquire, audio as video_audio, asr as video_asr, frames as video_frames
from src.video.config import VideoConfig
from src.video.report import render_report_html
from src.video.settings import VideoAgentSettings
from src.video.summary import generate_image_post_summary, generate_summary
from src.video.transcript import (
    estimate_text_tokens,
    merge_chunk_results,
    merge_caption_and_asr,
    merge_resume_results,
)

# 每任务最大运行时长
_TASK_TIMEOUT_SECONDS = 1800

# ASR 并发 worker 数
_ASR_WORKERS = 3

# 字幕覆盖率达标阈值（免 ASR）
_CAPTION_MIN_COVERAGE = 0.85

# 成本参考价（元/音频秒；以火山引擎计费规则为准，此处为历史参考值）
REFERENCE_PRICE_CNY_PER_SECOND = 0.00022
TOKEN_PRICE_CNY_PER_TOKEN = 0.00010875

# 任务状态常量
STATUS_SUBMITTED = "submitted"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


class VideoTaskError(RuntimeError):
    """视频任务领域错误（对外以 AppError 包装）。"""


def _iso_ts(value: object) -> float | None:
    """把 ISO 时间串转成秒级时间戳；解析失败返回 None。"""
    if not value:
        return None
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


class VideoTaskManager:
    """单机任务管理器：文件持久化 + asyncio 后台执行。"""

    def __init__(
        self,
        config: VideoConfig,
        get_settings: Callable[[], VideoAgentSettings],
        app=None,
    ) -> None:
        self._config = config
        self._get_settings = get_settings
        self._app = app
        self._lock = threading.Lock()
        self._running: dict[str, asyncio.Task] = {}
        # 事件流：按 task_id 保存内存事件缓冲 + SSE 订阅者队列
        self._event_buffers: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

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

    # ── MySQL 镜像（文件系统仍是实时状态源，DB 用于存档/统计） ──

    async def persist_to_db(self, state: dict[str, Any] | str) -> None:
        """把任务状态写入 MySQL；失败只记日志，不阻断流水线。"""
        if self._app is None:
            return
        session_factory = getattr(self._app.state, "session_factory", None)
        if session_factory is None:
            return
        if isinstance(state, str):
            state = self._load(state) or {}
        if not state:
            return
        try:
            async with session_factory() as session:
                await VideoTaskRepository(session).upsert_state(state)
        except Exception as exc:  # noqa: BLE001 — DB 镜像失败不影响解析任务
            import logging

            logging.getLogger(__name__).warning("video task DB persist failed: %s", exc)

    async def delete_from_db(self, task_id: str) -> None:
        if self._app is None:
            return
        session_factory = getattr(self._app.state, "session_factory", None)
        if session_factory is None:
            return
        try:
            async with session_factory() as session:
                await VideoTaskRepository(session).delete(task_id)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning("video task DB delete failed: %s", exc)

    def _update_stage(self, task_id: str, stage: str) -> None:
        state = self._load(task_id)
        if state is not None:
            state["stage"] = stage
            self._save(state)

    # ── 事件日志 / SSE ──

    @staticmethod
    def _sse_event(name: str, data: object) -> str:
        import json as _json

        return f"event: {name}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"

    def _emit_event(
        self,
        task_id: str,
        stage: str,
        title: str,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """记录一条流水线事件并推送给 SSE 订阅者。"""
        state = self._load(task_id)
        if state is None:
            return None
        events = state.get("events")
        if not isinstance(events, list):
            events = []
        event = {
            "seq": len(events) + 1,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "stage": stage,
            "title": title,
            "message": message,
            "level": level,
            "data": data or {},
        }
        events.append(event)
        state["events"] = events
        state["stage"] = stage
        self._save(state)

        buffer = self._event_buffers.setdefault(task_id, [])
        buffer.append(event)
        for queue in list(self._subscribers.get(task_id, set())):
            queue.put_nowait(event)
        return event

    def _close_event_stream(self, task_id: str) -> None:
        """通知该任务所有 SSE 订阅者结束（terminal 状态后调用）。"""
        for queue in list(self._subscribers.get(task_id, set())):
            queue.put_nowait(None)

    async def event_stream(self, task_id: str):
        """SSE 事件流：先回放历史事件，再实时推送直到任务结束。"""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(task_id, set()).add(queue)
        try:
            state = self._load(task_id)
            history = (state.get("events") or []) if state else []
            for event in history:
                yield self._sse_event("event", event)

            if state and state.get("status") in (STATUS_COMPLETE, STATUS_FAILED):
                yield self._sse_event("done", state)
                return

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield self._sse_event("heartbeat", {"ts": datetime.now(UTC).isoformat(timespec="seconds")})
                    continue
                if item is None:
                    yield self._sse_event("done", self._load(task_id) or {})
                    break
                yield self._sse_event("event", item)
        finally:
            self._subscribers.get(task_id, set()).discard(queue)

    # ── QA 历史 ──

    def get_qa_history(self, task_id: str) -> list[dict[str, Any]]:
        state = self._load(task_id)
        history = (state.get("qa_history") or []) if state else []
        return list(history) if isinstance(history, list) else []

    def append_qa_message(self, task_id: str, role: str, content: str) -> None:
        state = self._load(task_id)
        if state is None:
            return
        history = state.get("qa_history")
        if not isinstance(history, list):
            history = []
        history.append({
            "role": role,
            "content": content,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
        })
        # 只保留最近 40 条，避免状态文件无限膨胀
        state["qa_history"] = history[-40:]
        self._save(state)

    # ── 任务提交 ──

    def submit(
        self, source: str, *, kind: str = "url", frames: int = 12, content_type: str = "auto"
    ) -> dict[str, Any]:
        """登记新任务并返回初始状态。调用方（router）负责启动后台执行。

        content_type: auto | video | image_text
        """
        task_id = uuid.uuid4().hex[:16]
        state: dict[str, Any] = {
            "task_id": task_id,
            "source": source,
            "kind": kind,
            "content_type": content_type,
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
            "summary": None,
            "pid": None,
            "frames_requested": frames,
            "video_path": None,
            "audio_path": None,
            "post_text": None,
            "author": None,
            "hashtags": None,
            "publish_time": None,
            "post_images": [],
            "image_captions": None,
            "events": [],
            "qa_history": [],
        }
        self._save(state)
        self._event_buffers[task_id] = []
        return state

    # ── 后台执行 ──

    async def run(
        self, task_id: str, source: str, *, frames: int = 12, kind: str = "url", content_type: str = "auto"
    ) -> None:
        try:
            await asyncio.wait_for(
                self._run_pipeline(
                    task_id, source, frames=frames, kind=kind, content_type=content_type
                ),
                timeout=_TASK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await self._fail(task_id, f"任务超时（{_TASK_TIMEOUT_SECONDS // 60} 分钟）")
        except asyncio.CancelledError:
            await self._fail(task_id, "任务已取消")
            raise
        except Exception as exc:  # noqa: BLE001 — 任务失败统一落盘
            await self._fail(task_id, f"任务执行异常: {exc}")

    async def _run_pipeline(
        self, task_id: str, source: str, *, frames: int, kind: str, content_type: str = "auto"
    ) -> None:
        state = self._load(task_id)
        if state is None:
            return
        state["status"] = STATUS_RUNNING
        state["stage"] = "starting"
        state["content_type"] = content_type or "auto"
        self._save(state)
        self._emit_event(
            task_id, "starting", "启动解析",
            f"开始解析：{source}",
            data={"kind": kind, "frames": frames},
        )

        settings = self._get_settings()
        output_root = self._config.output_root / task_id
        output_root.mkdir(parents=True, exist_ok=True)
        # 提前写入输出目录，运行中关键帧接口即可访问
        state["output_dir"] = str(output_root)
        self._save(state)
        started = time.perf_counter()

        # 先识别内容类型：自动/视频/图文
        resolved_type = await self._resolve_content_type(
            task_id, source, kind, content_type,
            output_root=output_root,
            state=state,
        )
        if resolved_type == "unknown":
            raise VideoTaskError(
                "无法自动识别链接是视频还是图文，请在提交时手动选择「视频」或「图文」后重试。"
            )
        state = self._load(task_id) or {}
        state["content_type"] = resolved_type
        self._save(state)

        try:
            if resolved_type == "image_text":
                await self._run_image_pipeline(
                    task_id, source, settings, output_root, started,
                )
            else:
                await self._pipeline_body(
                    task_id, source, frames, kind, settings, output_root, started,
                    state,
                )
        except Exception:
            # 失败收尾：等待关键帧协程完成（若有），并把产物写入状态
            frames_task = getattr(self, "_pending_frames", None)
            if frames_task is not None and not frames_task.done():
                try:
                    kf = await frames_task
                except Exception:
                    kf = []
                s = self._load(task_id) or {}
                if kf:
                    s["keyframes"] = kf
                s["output_dir"] = str(output_root)
                self._save(s)
            raise

        # ── 收尾：写入状态 ──
        state = self._load(task_id) or {}
        state["status"] = STATUS_COMPLETE
        state["stage"] = "complete"
        state["output_dir"] = str(output_root)
        state["report"] = self._final_report
        state["transcript"] = self._final_transcript
        state["keyframes"] = self._final_keyframes
        state["cost"] = (self._final_report or {}).get("cost") or {}
        state["summary"] = self._final_summary
        state["transcript_source"] = self._final_transcript_source
        state["video_path"] = state.get("video_path") or getattr(self, "_final_video_path", None)
        state["audio_path"] = state.get("audio_path") or getattr(self, "_final_audio_path", None)
        self._save(state)
        if state.get("content_type") == "image_text":
            complete_msg = (
                f"已解析图文：{len(state.get('post_images') or [])} 张图片、"
                f"正文 {len(state.get('post_text') or '')} 字，已生成图文摘要。"
            )
            complete_data: dict[str, Any] = {
                "content_type": "image_text",
                "images": len(state.get("post_images") or []),
            }
        else:
            complete_msg = (
                f"已生成转录 {len(self._final_transcript or '')} 字、"
                f"{len(self._final_keyframes or [])} 张关键帧、摘要与 HTML 报告。"
            )
            complete_data = {
                "duration_seconds": (self._final_report or {}).get("duration_seconds"),
                "cost": (self._final_report or {}).get("cost"),
            }
        self._emit_event(
            task_id, "complete", "解析完成",
            complete_msg,
            level="success",
            data=complete_data,
        )
        self._close_event_stream(task_id)
        await self.persist_to_db(task_id)

        # 输出目录落一份自包含 manifest + transcript（视频库/外部工具可读）
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            manifest_out: dict[str, Any] = {
                "task_id": task_id,
                "source": source,
                "kind": state.get("kind") or "url",
                "created_at": state.get("created_at") or "",
                "output_dir": str(output_root),
                "status": STATUS_COMPLETE,
                "transcript": self._final_transcript,
                "transcript_source": self._final_transcript_source,
                "report": self._final_report or {},
                "summary": self._final_summary or {},
                "keyframes": self._final_keyframes,
                "cost": (self._final_report or {}).get("cost") or {},
                "video_path": getattr(self, "_final_video_path", None),
                "audio_path": getattr(self, "_final_audio_path", None),
                "content_type": state.get("content_type") or "video",
                "post_text": state.get("post_text") or None,
                "author": state.get("author") or None,
                "hashtags": state.get("hashtags") or None,
                "publish_time": state.get("publish_time") or None,
                "post_images": state.get("post_images") or None,
                "image_captions": state.get("image_captions") or None,
            }
            (output_root / "manifest.json").write_text(
                json.dumps(manifest_out, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (output_root / "transcript.txt").write_text(
                (self._final_transcript or "") + "\n", encoding="utf-8"
            )
        except OSError:
            pass

    # ── 类型识别与图文流水线 ──

    async def _resolve_content_type(
        self,
        task_id: str,
        source: str,
        kind: str,
        content_type: str,
        *,
        output_root: Path,
        state: dict[str, Any],
    ) -> str:
        """自动识别视频/图文；已指定类型则直接返回。"""
        if kind == "file":
            return "video"
        if content_type in ("video", "image_text"):
            return content_type

        self._update_stage(task_id, "detecting")
        self._emit_event(
            task_id, "detecting", "识别内容类型",
            "正在自动判断该链接是视频还是图文…",
        )
        try:
            info = await asyncio.to_thread(
                acquire.probe_media_info,
                source,
                45.0,
                referer=source,
                cookies=self._config.cookie_file,
                no_proxy=acquire.is_weixin_sph_url(source),
            )
            detected = acquire.classify_media_type(info)
            # 探测结果写回 state，供后续图文流水线再次使用（避免重复探测）
            state["content_type"] = detected
            state["_probe_info_ready"] = True
            self._save(state)
            self._emit_event(
                task_id, "detected", "类型识别完成",
                f"识别为：{'图文' if detected == 'image_text' else '视频' if detected == 'video' else '未知'}",
                level="success" if detected != "unknown" else "warning",
                data={"content_type": detected},
            )
            return detected
        except Exception as exc:  # noqa: BLE001
            self._emit_event(
                task_id, "detect_failed", "类型识别失败",
                f"无法自动识别内容类型：{exc}",
                level="warning",
            )
            return "unknown"

    async def _run_image_pipeline(
        self,
        task_id: str,
        source: str,
        settings: VideoAgentSettings,
        output_root: Path,
        started: float,
    ) -> None:
        """图文/图片帖流水线：抓取元信息 + 下载图片 + 多模态图文摘要。"""
        timings: dict[str, float] = {}

        self._update_stage(task_id, "fetching_image_post")
        self._emit_event(
            task_id, "fetching_image_post", "抓取图文内容",
            f"正在通过 yt-dlp 提取图文帖信息…",
            data={"source": source},
        )
        info = await asyncio.to_thread(
            acquire.probe_media_info,
            source,
            60.0,
            referer=source,
            cookies=self._config.cookie_file,
            no_proxy=acquire.is_weixin_sph_url(source),
        )
        meta = acquire.extract_image_post_meta(info)

        images_dir = output_root / "images"
        images = await asyncio.to_thread(
            acquire.download_image_post,
            source,
            info,
            images_dir,
            120.0,
            referer=source,
            cookies=self._config.cookie_file,
        )

        state = self._load(task_id) or {}
        state["content_type"] = "image_text"
        state["post_text"] = str(meta.get("post_text") or "")
        state["author"] = str(meta.get("author") or "")
        state["hashtags"] = list(meta.get("hashtags") or [])
        state["publish_time"] = str(meta.get("publish_time") or "")
        state["post_images"] = images
        state["output_dir"] = str(output_root)
        self._save(state)

        self._emit_event(
            task_id, "images_downloaded", "图文抓取完成",
            f"共下载 {len(images)} 张图片，标题长度 {len(str(meta.get('post_text') or ''))} 字。",
            level="success",
            data={"images": len(images), "post_text_len": len(str(meta.get("post_text") or ""))},
        )

        # 多模态图文摘要（失败不致命）
        self._update_stage(task_id, "visual_understanding")
        self._emit_event(
            task_id, "visual_understanding", "理解图文内容",
            f"正在用多模态模型阅读 {len(images)} 张图片与正文…",
            data={"images": len(images)},
        )
        try:
            summary = await generate_image_post_summary(
                str(meta.get("post_text") or ""),
                images,
                settings,
                self._app,
                title_hint=str(meta.get("title") or ""),
                output_path=output_root / "summary.json",
            )
            self._emit_event(
                task_id, "summary_completed", "图文摘要生成完成",
                f"摘要 {len(summary.get('summary') or '')} 字，图片说明 {len(summary.get('image_captions') or {})} 张。",
                level="success",
                data={"summary_chars": len(summary.get("summary") or ""), "captions": len(summary.get("image_captions") or {})},
            )
        except Exception as exc:  # noqa: BLE001
            summary = {}
            self._emit_event(
                task_id, "summary_failed", "图文摘要生成失败",
                f"已降级为仅图文展示：{exc}",
                level="warning",
            )

        timings["total"] = round(time.perf_counter() - started, 3)
        report: dict[str, Any] = {
            "task_id": task_id,
            "source": source,
            "status": "complete",
            "content_type": "image_text",
            "title": str(meta.get("title") or ""),
            "author": str(meta.get("author") or ""),
            "hashtags": list(meta.get("hashtags") or []),
            "publish_time": str(meta.get("publish_time") or ""),
            "images": images,
            "timings_seconds": timings,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "output_dir": str(output_root),
        }

        self._final_report = report
        self._final_transcript = ""
        self._final_keyframes = []
        self._final_summary = summary
        self._final_transcript_source = ""
        self._final_video_path = None
        self._final_audio_path = None

    async def _pipeline_body(
        self,
        task_id: str,
        source: str,
        frames: int,
        kind: str,
        settings: VideoAgentSettings,
        output_root: Path,
        started: float,
        state: dict[str, Any],
    ) -> None:
        # 流水线局部状态
        timings: dict[str, float] = {}
        transcript = ""
        transcript_source = ""
        acquisition_source = ""
        caption_ratio = 0.0
        caption_gaps: list[tuple[float, float]] = []
        asr_ranges: list[tuple[float, float]] = []
        duration = 0.0
        title = ""
        uploader = ""
        keyframes: list[dict[str, Any]] = []
        failed_chunks: list[int] = []
        partial = False
        no_speech_detected = False
        asr_billable_seconds = 0.0
        asr_usage_events: list[dict[str, Any]] = []
        chunk_timings: list[float] = []
        # 可播放媒体产物（输出目录内持久化，供右侧媒体面板预览/下载）
        self._final_video_path: str | None = None
        self._final_audio_path: str | None = None

        with tempfile.TemporaryDirectory(prefix="rag-video-") as tmp:
            work = Path(tmp)
            cues: list[dict[str, Any]] = []
            audio_path: Path | None = None
            direct_source = source
            download_referer: str | None = None
            browser_cookies: Path | None = None
            browser_referer: str | None = None

            # ── 获取阶段 ──
            if kind == "url":
                self._update_stage(task_id, "downloading")
                self._emit_event(
                    task_id, "downloading", "获取视频",
                    "正在解析视频源、尝试抓取平台字幕…",
                    data={"source": source},
                )
                if acquire.is_weixin_sph_url(source):
                    self._emit_event(
                        task_id, "resolving", "解析微信视频号",
                        "正在通过 wx_channels_download API 解析视频号直链…",
                    )
                    try:
                        resolved = await asyncio.to_thread(
                            acquire.resolve_weixin_source, source, 30.0
                        )
                        direct_source = str(resolved["url"])
                        download_referer = source
                        title = title or str(resolved.get("title") or "")
                        uploader = uploader or str(resolved.get("uploader") or "")
                        self._emit_event(
                            task_id, "resolved", "视频号解析成功",
                            f"已解析直链，标题：{title or '未知'}",
                            level="success",
                            data={"title": title, "uploader": uploader},
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._emit_event(
                            task_id, "resolve_failed", "视频号解析失败",
                            f"请确认已启动 wx_channels_download（未配 cookie 时需保持视频号页面打开）：{exc}",
                            level="error",
                        )
                        # 视频号没有公开直链，yt-dlp 无法兜底，直接给出可操作错误。
                        raise acquire.AcquisitionError(
                            "视频号解析失败：请先启动 wx_channels_download"
                            "（默认 API http://127.0.0.1:2022）。"
                            "若未配置 cloudflare.sphCookie，请保持一个视频号页面打开；"
                            "配置元宝 cookie 后可免手动打开每个用户链接。"
                            "或在环境中配置 WX_CHANNELS_API_BASE_URL 指向其 API 服务。"
                            f"原始错误：{exc}"
                        ) from exc
                elif acquire.is_douyin_url(source):
                    # 抖音精选页 /jingxuan?modal_id=... 不是 yt-dlp 支持的 URL；
                    # 提取 modal_id 并归一为标准视频页，否则会报 Unsupported URL。
                    normalized = acquire.normalize_douyin_url(direct_source)
                    if normalized != direct_source:
                        download_referer = source
                        direct_source = normalized
                        self._emit_event(
                            task_id, "normalized", "抖音链接归一",
                            f"已将分享页链接归一为 yt-dlp 支持格式：{direct_source}",
                            level="info",
                            data={"source": source, "normalized": direct_source},
                        )

                caption: dict[str, Any] = {}
                try:
                    caption = await asyncio.to_thread(
                        acquire.try_url_captions,
                        direct_source,
                        work / "captions",
                        45.0,
                        no_proxy=acquire.is_weixin_sph_url(source),
                        cookies=self._config.cookie_file,
                    )
                except Exception as exc:  # noqa: BLE001
                    caption = {"transcript": "", "cues": [], "duration": 0.0, "error": str(exc)}
                caption_text = str(caption.get("transcript") or "")
                cues = list(caption.get("cues") or [])
                duration = float(caption.get("duration") or duration)
                if not (acquire.is_weixin_sph_url(source) and title):
                    title = str(caption.get("title") or "") or title
                    uploader = str(caption.get("uploader") or "") or uploader
                coverage = acquire.caption_coverage(cues, duration)
                caption_ratio = float(coverage["ratio"])
                caption_gaps = list(coverage["gaps"])

                if caption_text and duration and not acquire.caption_needs_asr(coverage, _CAPTION_MIN_COVERAGE):
                    transcript = caption_text
                    transcript_source = "captions"
                    acquisition_source = "captions"
                    audio_path = None
                    self._update_stage(task_id, "captions_accepted")
                    self._emit_event(
                        task_id, "captions_accepted", "采用平台字幕",
                        f"字幕覆盖率达到 {caption_ratio:.0%}，无需 ASR 转写。",
                        level="success",
                        data={"ratio": caption_ratio, "chars": len(caption_text)},
                    )
                else:
                    if caption_text:
                        self._emit_event(
                            task_id, "captions_partial", "字幕覆盖不足",
                            f"平台字幕覆盖率仅 {caption_ratio:.0%}，将用 ASR 填补空白。",
                            level="warning",
                            data={"ratio": caption_ratio, "gaps": caption_gaps},
                        )
                    else:
                        self._emit_event(
                            task_id, "captions_not_found", "未找到可用字幕",
                            "将下载音频并走语音转写（ASR）。",
                        )
                    audio_path, acquisition_source, _browser_cues, browser_cookies, browser_referer = (
                        await asyncio.to_thread(
                            acquire.acquire_url_audio,
                            direct_source,
                            work,
                            300.0,
                            self._config.cookie_file,
                            download_referer,
                            (
                                (lambda: str(acquire.resolve_weixin_source(source, 30.0)["url"]))
                                if acquire.is_weixin_sph_url(source)
                                else None
                            ),
                            no_proxy=acquire.is_weixin_sph_url(source),
                        )
                    )
                    if not duration:
                        duration = await asyncio.to_thread(acquire.media_duration, audio_path)
                    self._update_stage(task_id, "audio_downloaded")
                    self._emit_event(
                        task_id, "audio_downloaded", "音频下载完成",
                        f"来源：{acquisition_source}，时长 {duration:.1f} 秒。",
                        level="success",
                        data={"duration_seconds": duration, "source": acquisition_source},
                    )
            else:
                # 本地文件
                local_path = Path(source).expanduser()
                if not local_path.is_file():
                    raise VideoTaskError(f"本地视频文件不存在: {local_path}")
                duration = await asyncio.to_thread(acquire.media_duration, local_path)
                audio_path = work / "audio.mp3"
                await asyncio.to_thread(acquire.extract_local_audio, local_path, audio_path, 180.0)
                acquisition_source = "local-file"
                if local_path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}:
                    self._final_video_path = str(local_path.resolve())
                    live_state = self._load(task_id)
                    if live_state is not None:
                        live_state["video_path"] = self._final_video_path
                        self._save(live_state)
                self._update_stage(task_id, "audio_extracted")
                self._emit_event(
                    task_id, "audio_extracted", "本地音频提取完成",
                    f"已提取音频，时长 {duration:.1f} 秒。",
                    level="success",
                    data={"duration_seconds": duration},
                )

            # 把音频产物持久化到输出目录，供右侧媒体面板预览/下载
            if audio_path is not None:
                output_root.mkdir(parents=True, exist_ok=True)
                audio_dest = output_root / "audio.mp3"
                await asyncio.to_thread(shutil.copy2, audio_path, audio_dest)
                self._final_audio_path = str(audio_dest)
                live_state = self._load(task_id)
                if live_state is not None:
                    live_state["audio_path"] = self._final_audio_path
                    self._save(live_state)

            # ── 并行：ASR 转写 与 关键帧提取 ──
            frames_task: asyncio.Task | None = None
            if frames > 0 and duration:
                frames_task = asyncio.create_task(
                    self._extract_frames(
                        task_id,
                        source,
                        direct_source,
                        frames,
                        duration,
                        browser_cookies,
                        browser_referer or download_referer,
                    )
                )
                self._pending_frames = frames_task

            if audio_path is not None:
                self._update_stage(task_id, "transcribing")
                self._emit_event(
                    task_id, "transcribing", "语音转写准备",
                    "正在检测静音、规划 ASR 分片…",
                )
                silence_points = await asyncio.to_thread(
                    video_audio.detect_silence_points, audio_path, 120.0
                )
                if cues:
                    coverage = acquire.caption_coverage(cues, duration)
                    caption_gaps = list(coverage["gaps"])
                    asr_ranges = acquire.choose_asr_ranges(coverage, duration, _CAPTION_MIN_COVERAGE)
                else:
                    asr_ranges = [(0.0, duration)]
                plan = video_audio.plan_asr_chunks(duration, silence_points, asr_ranges)

                if plan:
                    if not settings.asr_configured:
                        raise VideoTaskError(
                            "ASR 凭证未配置：请在 agent设置页或 .env 配置当前渠道凭证（"
                            "百炼 DASHSCOPE_ASR_API_KEY / 火山 VOLC_ASR_API_KEY）"
                        )
                    self._emit_event(
                        task_id, "asr_planned", "ASR 分片规划完成",
                        f"共 {len(plan)} 个分片，覆盖 {sum(e - s for _i, s, e in plan):.1f} 秒音频。",
                        data={"chunks": len(plan), "ranges": [[s, e] for _i, s, e in plan]},
                    )
                    # 切分所有分片
                    chunk_paths = await asyncio.to_thread(
                        video_audio.split_audio_plan,
                        audio_path,
                        work / "chunks",
                        plan,
                        240.0,
                    )
                    path_by_index = {idx: p for (idx, _s, _e), p in zip(plan, chunk_paths)}
                    range_by_index = {idx: (s, e) for idx, s, e in plan}
                    # 并发转写（信号量限流）
                    sem = asyncio.Semaphore(_ASR_WORKERS)

                    async def _one(idx: int, start: float) -> dict[str, Any]:
                        async with sem:
                            self._emit_event(
                                task_id, "asr_chunk_start", "ASR 转写分片",
                                f"开始转写第 {idx + 1}/{len(plan)} 片（{start:.1f}s 起）…",
                                data={"index": idx, "start": start},
                            )
                            result = await asyncio.to_thread(
                                video_asr.transcribe_chunk,
                                idx,
                                start,
                                path_by_index[idx],
                                settings,
                                120.0,
                                1,
                            )
                            ok = result.get("error") is None
                            text = str(result.get("text") or "")
                            snippet = " ".join(text.strip().split())[:80]
                            self._emit_event(
                                task_id,
                                "asr_chunk_done" if ok else "asr_chunk_failed",
                                "ASR 分片完成" if ok else "ASR 分片失败",
                                (
                                    f"第 {idx + 1} 片完成，转写 {len(text)} 字"
                                    + (f"：{snippet}…" if snippet else "") + "。"
                                    if ok
                                    else f"第 {idx + 1} 片失败：{result.get('error')}"
                                ),
                                level="success" if ok else "warning",
                                data={"index": idx, "chars": len(text), "preview": snippet, "error": result.get("error")},
                            )
                            return result

                    results = list(await asyncio.gather(*(_one(idx, start) for idx, start, _e in plan)))
                    results = merge_resume_results([], results)
                    for result in results:
                        result_duration = range_by_index[int(result["index"])][1] - range_by_index[int(result["index"])][0]
                        result["audio_seconds"] = round(result_duration, 3)
                        result["billable_audio_seconds"] = round(result_duration, 3) if not result.get("error") else 0.0
                        result["total_attempts"] = int(result.get("attempts") or 0)
                        if int(result.get("attempts") or 0):
                            asr_usage_events.append({
                                "chunk_index": int(result["index"]),
                                "api_calls": int(result["attempts"]),
                                "audio_seconds_per_call": round(result_duration, 3),
                                "billable_audio_seconds": result["billable_audio_seconds"],
                            })
                    chunk_timings = [float(r.get("elapsed_seconds") or 0) for r in sorted(results, key=lambda r: int(r["index"]))]
                    asr_billable_seconds = sum(float(e["billable_audio_seconds"]) for e in asr_usage_events)

                    chunk_durations: dict[int, float] = {idx: e - s for idx, s, e in plan}
                    merged = merge_chunk_results(results, chunk_durations)
                    transcript = (
                        merge_caption_and_asr(cues, results, chunk_durations)
                        if cues
                        else str(merged["transcript"])
                    )
                    completed = {int(r["index"]) for r in results if r.get("error") is None}
                    failed_chunks = sorted(set(list(merged["failed_chunks"]) + [i for i, _s, _e in plan if i not in completed]))
                    partial = bool(failed_chunks)
                    no_speech_detected = not partial and not transcript.strip()
                    transcript_source = (
                        f"captions + {settings.asr_model} gap fill"
                        if cues
                        else f"{settings.asr_model} ({len(plan)} VAD chunks, {_ASR_WORKERS} workers)"
                    )
                    self._emit_event(
                        task_id,
                        "asr_completed" if not partial else "asr_partial",
                        "语音转写完成" if not partial else "语音转写部分完成",
                        (
                            f"共转写 {len(transcript)} 字，失败分片 {len(failed_chunks)} 个。"
                            if partial
                            else f"共转写 {len(transcript)} 字。"
                        ),
                        level="warning" if partial else "success",
                        data={"chars": len(transcript), "failed_chunks": failed_chunks},
                    )
            elif caption_text:
                transcript = caption_text
                transcript_source = "captions"

            if frames_task is not None:
                keyframes = await frames_task
            if keyframes:
                self._update_stage(task_id, "visual_understanding")
                self._emit_event(
                    task_id, "visual_understanding", "理解视频画面",
                    f"正在用多模态模型读取 {len(keyframes)} 张关键帧，提取画面信息…",
                    data={"frames": len(keyframes)},
                )
            if not transcript.strip() and not no_speech_detected:
                raise VideoTaskError("未生成任何转录内容")

            # ── 摘要（Chat 模型；失败不致命）──
            self._update_stage(task_id, "summarizing")
            self._emit_event(
                task_id, "summarizing", "生成摘要与要点",
                f"正在基于 {len(transcript)} 字转录" +
                (f"、{len(keyframes)} 张关键帧" if keyframes else "") +
                "生成摘要…",
            )
            summary: dict[str, Any] = {}
            try:
                summary = await generate_summary(
                    transcript,
                    settings,
                    self._app,
                    title_hint=title or "",
                    keyframes=keyframes,
                    output_path=output_root / "summary.json",
                )
                visual_count = len(summary.get("visual_notes") or [])
                caption_count = len(summary.get("keyframe_captions") or {})
                self._emit_event(
                    task_id,
                    "summary_completed",
                    "摘要生成完成",
                    f"摘要 {len(summary.get('summary') or '')} 字，"
                    f"{len(summary.get('keypoints') or [])} 条要点"
                    + (f"，画面洞察 {visual_count} 条" if visual_count else "")
                    + (f"，关键帧说明 {caption_count} 张" if caption_count else "")
                    + "。",
                    level="success",
                    data={
                        "summary_chars": len(summary.get("summary") or ""),
                        "keypoints": len(summary.get("keypoints") or []),
                        "visual_notes": visual_count,
                        "keyframe_captions": caption_count,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                summary = {}
                self._emit_event(
                    task_id, "summary_failed", "摘要生成失败",
                    f"已降级为无摘要报告：{exc}",
                    level="warning",
                )

            # ── HTML 报告（失败不致命）──
            self._update_stage(task_id, "rendering_report")
            self._emit_event(
                task_id, "rendering_report", "渲染 HTML 报告",
                "正在生成自包含报告页面…",
            )
            report_id = state.get("task_id") or task_id
            report = self._build_report(
                task_id=report_id,
                source=source,
                status="partial" if partial else "complete",
                title=title,
                uploader=uploader,
                duration=duration,
                acquisition_source=acquisition_source,
                transcript_source=transcript_source,
                caption_ratio=caption_ratio,
                partial=partial,
                no_speech_detected=no_speech_detected,
                failed_chunks=failed_chunks,
                keyframes=keyframes,
                timings=timings,
                started=started,
                chunk_timings=chunk_timings,
                transcript=transcript,
                asr_billable_seconds=asr_billable_seconds,
                output_dir=str(output_root),
            )
            try:
                render_report_html(
                    output_path=output_root / "report.html",
                    report=report,
                    transcript=transcript,
                    keyframes=keyframes,
                    summary=summary,
                )
                report_path = output_root / "report.html"
                self._emit_event(
                    task_id, "report_completed", "HTML 报告已生成",
                    f"报告大小 {report_path.stat().st_size / 1024:.0f} KB。",
                    level="success",
                    data={"path": str(report_path), "bytes": report_path.stat().st_size},
                )
            except Exception as exc:  # noqa: BLE001 — 报告失败不影响结果
                report.setdefault("report_warning", f"报告渲染失败: {exc}")
                self._emit_event(
                    task_id, "report_failed", "HTML 报告渲染失败",
                    f"已保留转录/关键帧结果：{exc}",
                    level="warning",
                )

        # 结果交给 _run_pipeline 统一收尾
        self._final_report = report
        self._final_transcript = transcript
        self._final_keyframes = keyframes
        self._final_summary = summary
        self._final_transcript_source = transcript_source

    async def _extract_frames(
        self,
        task_id: str,
        source: str,
        direct_source: str,
        frames: int,
        duration: float,
        browser_cookies: Path | None,
        referer: str | None,
    ) -> list[dict[str, Any]]:
        """（必要时）下载低码率视频并提取关键帧；与 ASR 并行。"""
        kf: list[dict[str, Any]] = []
        video_for_frames: Path | None = None
        try:
            self._update_stage(task_id, "extracting_frames")
            self._emit_event(
                task_id, "frames_started", "提取关键帧",
                f"目标 {frames} 帧，与语音转写并行执行…",
                data={"frames_requested": frames},
            )
            if acquire.is_url(source):
                # 直接把低码率视频持久化到输出目录，右侧媒体面板可直接播放/下载
                video_out_dir = self._config.output_root / task_id
                video_out_dir.mkdir(parents=True, exist_ok=True)
                video_for_frames = await asyncio.to_thread(
                    acquire.download_url_video,
                    direct_source,
                    video_out_dir,
                    300.0,
                    referer=referer,
                    cookies=browser_cookies or self._config.cookie_file,
                    max_height=360,
                    no_proxy=acquire.is_weixin_sph_url(source),
                )
                self._final_video_path = str(video_for_frames.resolve())
                live_state = self._load(task_id)
                if live_state is not None:
                    live_state["video_path"] = self._final_video_path
                    self._save(live_state)
                self._emit_event(
                    task_id, "frames_downloaded", "关键帧视频已就绪",
                    "低码率视频下载完成，已保存到媒体面板，开始抽帧。",
                    level="success",
                    data={"video_path": self._final_video_path},
                )
            else:
                video_for_frames = Path(source).expanduser()
                if video_for_frames.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}:
                    self._final_video_path = str(video_for_frames.resolve())

            if video_for_frames is None:
                return kf

            frames_dir = Path(tempfile.gettempdir()) / f"rag-video-frames-{task_id}" / "out"
            frames_result, _meta = await asyncio.to_thread(
                video_frames.extract_keyframes,
                str(video_for_frames),
                frames_dir,
                512,
                frames,
                None,
                duration,
                True,
            )
            kf = [
                {"timestamp_seconds": f["timestamp_seconds"], "path": f["path"]}
                for f in frames_result
            ]
            # 复制到输出目录，保证自包含
            if kf:
                frames_dest = self._config.output_root / task_id / "frames"
                frames_dest.mkdir(parents=True, exist_ok=True)
                for kfi in kf:
                    src = Path(kfi["path"])
                    if src.is_file():
                        shutil.copy2(src, frames_dest / src.name)
                # path 指向输出目录副本
                for kfi in kf:
                    kfi["path"] = str(frames_dest / Path(kfi["path"]).name)
                # 提前写入状态，前端轮询/SSE 能实时看到关键帧缩略图
                live_state = self._load(task_id)
                if live_state is not None:
                    live_state["keyframes"] = kf
                    live_state["output_dir"] = live_state.get("output_dir") or str(self._config.output_root / task_id)
                    self._save(live_state)
            self._emit_event(
                task_id, "frames_extracted", "关键帧提取完成",
                f"共提取 {len(kf)} 张关键帧。",
                level="success" if kf else "info",
                data={"count": len(kf), "keyframes": kf},
            )
        except Exception as exc:  # noqa: BLE001 — 帧提取失败不致命
            self._update_stage(task_id, "frame_extract_failed")
            self._emit_event(
                task_id, "frames_failed", "关键帧提取失败",
                f"已降级为纯音频/无画面模式：{exc}",
                level="warning",
            )
        return kf

    @staticmethod
    def _build_report(**kw: Any) -> dict[str, Any]:
        started = kw["started"]
        timings = dict(kw["timings"])
        timings["total"] = round(time.perf_counter() - started, 3)
        timings["chunk_api"] = kw["chunk_timings"]
        transcript = kw["transcript"]
        usage = {
            "transcript_characters": len(transcript),
            "estimated_transcript_tokens": estimate_text_tokens(transcript),
            "image_tokens": 0,
            "summary_tokens": "unavailable",
        }
        cost = {
            "estimated_asr_cny": round(
                kw["asr_billable_seconds"] * REFERENCE_PRICE_CNY_PER_SECOND, 4
            ),
            "estimated_asr_audio_seconds": round(kw["asr_billable_seconds"], 3),
            "rate_reference_cny_per_audio_second": REFERENCE_PRICE_CNY_PER_SECOND,
            "estimated_transcript_token_cny": round(
                estimate_text_tokens(transcript) * TOKEN_PRICE_CNY_PER_TOKEN, 4
            ),
            "token_price_reference_cny_per_token": TOKEN_PRICE_CNY_PER_TOKEN,
        }
        return {
            "task_id": kw["task_id"],
            "source": kw["source"],
            "status": kw["status"],
            "title": kw["title"],
            "uploader": kw["uploader"],
            "duration_seconds": round(kw["duration"], 3) if kw["duration"] else "unavailable",
            "audio_only": True,
            "acquisition_source": kw["acquisition_source"],
            "transcript_source": kw["transcript_source"],
            "caption_coverage_ratio": kw["caption_ratio"],
            "partial": kw["partial"],
            "no_speech_detected": kw["no_speech_detected"],
            "failed_chunks": kw["failed_chunks"],
            "keyframes": kw["keyframes"],
            "timings_seconds": timings,
            "usage": usage,
            "cost": cost,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "report_id": kw["task_id"],
            "output_dir": kw["output_dir"],
        }

    async def _fail(self, task_id: str, message: str) -> None:
        state = self._load(task_id)
        if state is None:
            return
        state["status"] = STATUS_FAILED
        state["stage"] = "failed"
        state["error"] = message
        self._save(state)
        self._emit_event(
            task_id, "failed", "解析失败",
            message or "未知错误",
            level="error",
            data={"error": message},
        )
        self._close_event_stream(task_id)
        await self.persist_to_db(task_id)

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

    # ── 历史视频库（旧 quick-watch 输出目录 + 新流水线输出目录） ──

    @staticmethod
    def _classify_source(source: str) -> str:
        """按来源 URL/路径归类：weixin / bilibili / douyin / youtube / local / other。"""
        s = (source or "").strip()
        m = re.match(r"^https?://([^/]+)", s)
        if not m:
            return "local"
        host = m.group(1).lower()
        if "weixin.qq.com" in host or "finder.video.qq.com" in host or host.startswith("sph"):
            return "weixin"
        if "bilibili.com" in host or "b23.tv" in host:
            return "bilibili"
        if "douyin.com" in host or "iesdouyin.com" in host:
            return "douyin"
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        return "other"

    def _library_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self._config.library_root.exists():
            roots.append(self._config.library_root)
        if self._config.output_root.exists() and self._config.output_root != self._config.library_root:
            roots.append(self._config.output_root)
        return roots

    def list_library(self) -> list[dict[str, Any]]:
        """扫描视频库：旧 quick-watch 目录 + 本系统输出目录，读取每任务产物。"""
        roots = self._library_roots()
        if not roots:
            return []

        seen: set[str] = set()
        tasks: list[dict[str, Any]] = []
        for root in roots:
            entries: list[Path] = []
            for p in root.iterdir():
                # 先过滤再排序：latest 可能是悬空软链，stat() 会抛 FileNotFoundError
                if p.is_symlink() or not p.is_dir() or p.name.startswith("."):
                    continue
                if p.name in ("latest", "tasks.json", "knowledge_index.ndjson"):
                    continue
                entries.append(p)
            for entry in entries:
                if entry.name in seen:
                    continue
                seen.add(entry.name)
                task = self._scan_library_task(entry)
                if task is not None:
                    tasks.append(task)

        tasks.sort(key=lambda t: _iso_ts(t.get("created_at")) or 0.0, reverse=True)
        return tasks

    def delete_library_task(self, task_dir: str) -> Path:
        """删除视频库中的一个任务目录（不可恢复）。

        路径安全：task_dir 仅允许 [A-Za-z0-9._-]，且解析后必须位于
        library_root / output_root 之内（防路径穿越）。同时清理所在根的 tasks.json。
        """
        import re as _re
        import shutil as _shutil

        if not _re.fullmatch(r"[A-Za-z0-9._-]+", task_dir):
            raise VideoTaskError(f"非法的任务目录名: {task_dir!r}")
        target: Path | None = None
        target_root: Path | None = None
        for root in self._library_roots():
            candidate = (root / task_dir).resolve()
            if str(candidate).startswith(str(root.resolve()) + os.sep) and candidate.is_dir():
                target = candidate
                target_root = root
                break
        if target is None or target_root is None:
            raise VideoTaskError(f"任务目录不存在: {task_dir}")
        _shutil.rmtree(target, ignore_errors=False)

        # 从所在根的 tasks.json 索引移除该任务条目
        index_path = target_root / "tasks.json"
        if index_path.is_file():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
                if isinstance(index, list):
                    kept = [
                        item for item in index
                        if not (isinstance(item, dict) and str(item.get("output_dir") or "").endswith(target.name))
                    ]
                    if len(kept) != len(index):
                        index_path.write_text(
                            json.dumps(kept, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
            except (json.JSONDecodeError, OSError):
                pass
        return target

    def _scan_library_task(self, task_dir: Path) -> dict[str, Any] | None:
        """读取单个任务目录，构造展示数据。

        兼容两种布局：
        - 旧 quick-watch 交付物（manifest.json + summary.json + frames/ + report.html）
        - 本系统流水线输出（manifest.json / summary.json / report.html / frames/ / transcript.txt）
        无任何产物的目录（如误放的任务嵌套目录）返回 None 跳过。
        """
        manifest_path = task_dir / "manifest.json"
        summary_path = task_dir / "summary.json"
        report_html = task_dir / "report.html"
        transcript_path = task_dir / "transcript.txt"
        frames_dir = task_dir / "frames"

        has_any = (
            manifest_path.exists()
            or summary_path.exists()
            or report_html.exists()
            or (frames_dir.is_dir() and any(frames_dir.glob("*.jpg")))
        )
        if not has_any:
            return None

        data: dict[str, Any] = {
            "task_id": task_dir.name,
            "output_dir": str(task_dir),
            "title": task_dir.name,
            "summary": "",
            "keypoints": [],
            "transcript": "",
            "keyframes": [],
            "has_report": report_html.exists(),
            "created_at": None,
            "source": "",
            "duration_seconds": None,
            "cost": None,
            "source_kind": "other",
        }

        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    manifest = {}
            except (json.JSONDecodeError, OSError):
                manifest = {}
            data["source"] = str(manifest.get("source") or "")
            data["created_at"] = str(manifest.get("created_at") or "")
            report = manifest.get("report") or {}
            if isinstance(report, dict):
                data["title"] = str(report.get("title") or "") or data["title"]
                data["duration_seconds"] = report.get("duration_seconds")
                data["keyframes"] = report.get("keyframes") or []
                data["cost"] = report.get("cost")
                data["transcript_source"] = report.get("transcript_source")
            # 本系统 manifest：transcript 在顶层
            if not data["transcript"]:
                data["transcript"] = str(manifest.get("transcript") or "")

        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                summary = {}
            data["summary"] = str(summary.get("summary") or "")
            data["keypoints"] = list(summary.get("keypoints") or [])
            if summary.get("title_override"):
                data["title"] = str(summary["title_override"])
            data["visual_notes"] = list(summary.get("visual_notes") or [])

        # 转录：transcript.txt（本系统）→ 缓存 manifest（旧 quick-watch）
        if not data["transcript"] and transcript_path.is_file():
            try:
                data["transcript"] = transcript_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        fingerprint = manifest.get("fingerprint") if isinstance(manifest, dict) else None
        if not data["transcript"] and fingerprint:
            cache_path = Path.home() / ".cache" / "quick-watch" / "tasks" / str(fingerprint) / "manifest.json"
            if cache_path.exists():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                    data["transcript"] = str(cache.get("transcript") or "")
                except (json.JSONDecodeError, OSError):
                    pass

        # 兜底：本系统任务状态文件（task_root/<id>.json）——旧输出目录可能没有 manifest
        if not data["source"]:
            state_path = self._config.task_root / f"{task_dir.name}.json"
            if state_path.is_file():
                try:
                    st = json.loads(state_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    st = {}
                if isinstance(st, dict):
                    data["source"] = str(st.get("source") or "")
                    data["created_at"] = str(st.get("created_at") or "") or data["created_at"]
                    data["transcript_source"] = st.get("transcript_source") or data.get("transcript_source")
                    data["cost"] = st.get("cost") or data["cost"]
                    if not data["transcript"]:
                        data["transcript"] = str(st.get("transcript") or "")
                    report = st.get("report")
                    if isinstance(report, dict):
                        data["title"] = str(report.get("title") or "") or data["title"]
                        data["duration_seconds"] = report.get("duration_seconds") or data["duration_seconds"]
                        data["keyframes"] = report.get("keyframes") or data["keyframes"]
                    summary = st.get("summary")
                    if isinstance(summary, dict):
                        if not data["summary"]:
                            data["summary"] = str(summary.get("summary") or "")
                        if not data["keypoints"]:
                            data["keypoints"] = list(summary.get("keypoints") or [])
                        if summary.get("title_override"):
                            data["title"] = str(summary["title_override"])
                        data["visual_notes"] = list(summary.get("visual_notes") or [])

        # created_at 兜底：目录 mtime（保证排序可见）
        if not data["created_at"]:
            try:
                from datetime import datetime as _dt

                data["created_at"] = _dt.fromtimestamp(task_dir.stat().st_mtime).isoformat(timespec="seconds")
            except OSError:
                pass

        # 关键帧文件实际路径映射
        resolved_frames = []
        for kf in data["keyframes"]:
            if not isinstance(kf, dict):
                continue
            path = str(kf.get("path") or "")
            resolved_frames.append({**kf, "path": path})
        if not resolved_frames and frames_dir.is_dir():
            for f in sorted(frames_dir.glob("*.jpg")):
                resolved_frames.append({"path": str(f), "timestamp_seconds": 0})
        data["keyframes"] = resolved_frames

        # 来源分类：微信视频号 / B站 / 抖音 / YouTube / 本地 / 其他
        data["source_kind"] = self._classify_source(str(data.get("source") or ""))
        return data

    # ── 启动 / 停止 ──

    def start_background(
        self, task_id: str, source: str, *, frames: int = 12, kind: str = "url", content_type: str = "auto"
    ) -> None:
        """启动后台任务（在事件循环内调度）。"""
        loop = asyncio.get_event_loop()
        task = loop.create_task(
            self.run(task_id, source, frames=frames, kind=kind, content_type=content_type)
        )
        self._running[task_id] = task
        task.add_done_callback(lambda _t: self._running.pop(task_id, None))

    def shutdown(self) -> None:
        """取消所有运行中任务（应用关闭时调用）。"""
        for task in list(self._running.values()):
            task.cancel()
        self._running.clear()
