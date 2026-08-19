"""摘要生成：Chat 模型（豆包方舟或系统 model_relay）生成 summary.json。

支持多模态：
- 当视频 agent 配置了独立 Chat 通道（chat_configured=True，例如豆包 2.1 Pro）
  且有关键帧时，把关键帧图片以 OpenAI 兼容 content 数组发给模型，
  生成画面洞察（visual_notes）和关键帧说明（keyframe_captions）。
- 未配置独立通道 / 模型不支持图片时自动降级为纯转录摘要，不阻断流水线。

摘要生成失败不致命：调用方降级为「无摘要」报告。
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from pydantic import SecretStr

from src.models.llm import ChatClient
from src.video.settings import VideoAgentSettings

logger = logging.getLogger(__name__)

# 转录输入截断字符数（避免超长上下文）
_TRANSCRIPT_LIMIT = 12000
# 一次最多送入视觉模型的关键帧数量（超过时均匀抽样）
_MAX_VISUAL_FRAMES = 8

_SUMMARY_SYSTEM_PROMPT = (
    "你是视频解析助手。下面是视频的完整转录文本（带 [MM:SS] 时间戳）。"
    "请基于转录内容提炼视频的核心信息，只输出一个 JSON 对象（不要输出其他文字）：\n"
    '{"title": "不超过30字的视频标题", '
    '"summary": "3-5 句话的中文摘要，概括视频主旨与关键内容", '
    '"keypoints": ["3-6 条要点，每条 1-2 句话，尽量引用时间戳说明出处"]}\n\n'
    "【视频转录】\n"
)

_SUMMARY_VISION_SYSTEM_PROMPT = (
    "你是视频解析助手。下面是视频的完整转录文本（带 [MM:SS] 时间戳），"
    "随后还有若干张从视频中抽取的关键帧图片。"
    "请把语音内容与画面内容融合分析，只输出一个 JSON 对象（不要输出其他文字）：\n"
    '{"title": "不超过30字的视频标题", '
    '"summary": "3-5 句话的中文摘要，概括视频主旨与关键内容", '
    '"keypoints": ["3-6 条要点，每条 1-2 句话，尽量引用时间戳说明出处"], '
    '"visual_notes": ["画面上可见但音频未提到的内容，如 PPT 标题、图表数值、UI 状态、操作步骤"], '
    '"keyframe_captions": {"MM:SS": "该关键帧的一句话画面说明"}}\n\n'
    "注意：keyframe_captions 的键必须是关键帧对应的时间戳（MM:SS 或 H:MM:SS）；"
    "没有把握的画面细节不要编造。\n"
    "【视频转录】\n"
)


def make_chat_client(
    settings: VideoAgentSettings,
    app,
    *,
    model: str | None = None,
    qa: bool = False,
) -> tuple[ChatClient, bool]:
    """构造 ChatClient。

    model 可覆盖 settings.chat_model / qa_model（例如问答用 Turbo、摘要用 Pro）。
    qa=True 时优先使用问答模型的独立 Base URL / API Key；缺省字段回退到摘要配置。
    返回 (client, owns_client)。owns_client=True 时调用方负责关闭底层
    httpx client（独立通道）；False 时复用系统共享 client，不要关闭。
    """
    if qa:
        base_url = settings.qa_base_url.strip() or settings.chat_base_url
        api_key = settings.qa_api_key.strip() or settings.chat_api_key
        chat_model = model or settings.qa_model or settings.chat_model
        configured = settings.qa_configured
    else:
        base_url = settings.chat_base_url
        api_key = settings.chat_api_key
        chat_model = model or settings.chat_model
        configured = settings.chat_configured

    if configured:
        view = SimpleNamespace(
            base_url=base_url.rstrip("/"),
            api_key=SecretStr(api_key),
            chat_model=chat_model,
        )
        shell = type("_VideoChatShell", (), {"model_relay": view})()
        # 豆包 2.1 Pro 生成完整摘要可能较慢（实测长转录约 70-120s），给足超时
        client = httpx.AsyncClient(timeout=300.0)
        return ChatClient(shell, client), True
    return (
        ChatClient(app.state.settings_shell, app.state.model_relay_client._client),
        False,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON 对象（容忍 markdown 代码块/前后杂讯）。"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _image_to_data_url(path: str | Path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    mime = "image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _sample_keyframes(
    keyframes: list[dict[str, Any]], limit: int = _MAX_VISUAL_FRAMES
) -> list[dict[str, Any]]:
    """均匀抽样关键帧，避免一次请求塞入过多图片。"""
    if not keyframes:
        return []
    if len(keyframes) <= limit:
        return list(keyframes)
    step = len(keyframes) / limit
    picked: list[dict[str, Any]] = []
    for i in range(limit):
        picked.append(keyframes[int(i * step)])
    return picked


def _build_visual_messages(
    transcript: str,
    keyframes: list[dict[str, Any]],
    title_hint: str = "",
) -> list[dict[str, Any]]:
    """构造多模态 messages：转录 + 抽样关键帧图片。"""
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _SUMMARY_VISION_SYSTEM_PROMPT
            + (transcript or "")[:_TRANSCRIPT_LIMIT]
            + (
                f"\n\n视频标题提示（可为空）：{title_hint}\n"
                if title_hint
                else "\n"
            )
            + "\n以下是关键帧图片，按顺序与 keyframe_captions 键对应：",
        }
    ]
    for kf in _sample_keyframes(keyframes):
        url = _image_to_data_url(str(kf.get("path") or ""))
        if url:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
    return [
        {"role": "system", "content": "你是视频解析助手，请严格按照用户要求输出 JSON。"},
        {"role": "user", "content": content},
    ]


def _normalize_summary(
    data: dict[str, Any] | None,
    *,
    title_hint: str = "",
    keyframes: list[dict[str, Any]],
) -> dict[str, Any]:
    """把模型 JSON 归一化成 summary.json 结构。"""
    data = data or {}
    summary: dict[str, Any] = {
        "title": str(data.get("title") or "").strip() or title_hint,
        "summary": str(data.get("summary") or "").strip(),
        "keypoints": [
            str(k).strip() for k in (data.get("keypoints") or []) if str(k).strip()
        ],
        "visual_notes": [
            str(v).strip() for v in (data.get("visual_notes") or []) if str(v).strip()
        ],
        "keyframe_captions": {},
        "mode": "summary",
    }
    raw_captions = data.get("keyframe_captions")
    if isinstance(raw_captions, dict):
        captions: dict[str, str] = {}
        for ts, cap in raw_captions.items():
            cap_text = str(cap).strip()
            if cap_text:
                captions[str(ts).strip()] = cap_text
        summary["keyframe_captions"] = captions
    # 没有返回画面说明时，为已有关键帧补空占位（保持结构稳定）
    if keyframes and not summary["keyframe_captions"]:
        summary["keyframe_captions"] = {}
    return summary


async def generate_summary(
    transcript: str,
    settings: VideoAgentSettings,
    app,
    *,
    title_hint: str = "",
    keyframes: list[dict[str, Any]] | None = None,
    output_path=None,
) -> dict[str, Any]:
    """用 Chat 模型生成摘要并写入 output_dir/summary.json。

    返回 summary dict（{title, summary, keypoints, visual_notes,
    keyframe_captions, mode}），失败返回 {}。
    """
    text = (transcript or "").strip()
    keyframes = list(keyframes or [])
    if not text:
        return {}
    client, owns = make_chat_client(settings, app)
    try:
        # 优先多模态：独立 Chat 通道 + 有关键帧时尝试图片输入
        if settings.chat_configured and keyframes:
            try:
                messages = _build_visual_messages(text, keyframes, title_hint)
                answer = await client.complete(messages, max_tokens=2000)
                data = _extract_json(answer) or {}
                summary = _normalize_summary(data, title_hint=title_hint, keyframes=keyframes)
                if output_path is not None:
                    _write_summary(output_path, summary)
                return summary
            except Exception as exc:  # noqa: BLE001 — 多模态失败降级纯文本
                logger.warning("video multimodal summary failed, fallback to text: %s", exc)

        messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT + text[:_TRANSCRIPT_LIMIT]},
            {"role": "user", "content": "请输出摘要 JSON。"},
        ]
        answer = await client.complete(messages, max_tokens=2000)
        data = _extract_json(answer) or {}
        summary = _normalize_summary(data, title_hint=title_hint, keyframes=[])
        if output_path is not None:
            _write_summary(output_path, summary)
        return summary
    except Exception as exc:  # noqa: BLE001 — 摘要失败不致命
        logger.warning("video summary generation failed: %s", exc)
        return {}
    finally:
        if owns:
            try:
                await client._client.aclose()
            except Exception:
                pass


def _write_summary(output_path, summary: dict[str, Any]) -> None:
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("write summary.json failed: %s", exc)
