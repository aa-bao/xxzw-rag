"""摘要生成：Chat 模型（豆包方舟或系统 model_relay）生成 summary.json。

Chat 配置语义：
- 视频 agent chat 独立配置（chat_base_url/chat_model/chat_api_key 齐备）时用独立通道；
- 否则回退系统 model_relay（与问答原实现一致）。

摘要生成失败不致命：调用方降级为「无摘要」报告。
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from src.models.llm import ChatClient
from src.video.settings import VideoAgentSettings

logger = logging.getLogger(__name__)

# 转录输入截断字符数（避免超长上下文）
_TRANSCRIPT_LIMIT = 12000

_SUMMARY_SYSTEM_PROMPT = (
    "你是视频解析助手。下面是视频的完整转录文本（带 [MM:SS] 时间戳）。"
    "请基于转录内容提炼视频的核心信息，只输出一个 JSON 对象（不要输出其他文字）：\n"
    '{"title": "不超过30字的视频标题", '
    '"summary": "3-5 句话的中文摘要，概括视频主旨与关键内容", '
    '"keypoints": ["3-6 条要点，每条 1-2 句话，尽量引用时间戳说明出处"]}\n\n'
    "【视频转录】\n"
)


def make_chat_client(
    settings: VideoAgentSettings, app
) -> tuple[ChatClient, bool]:
    """构造 ChatClient。

    返回 (client, owns_client)。owns_client=True 时调用方负责关闭底层
    httpx client（独立通道）；False 时复用系统共享 client，不要关闭。
    """
    if settings.chat_configured:
        view = SimpleNamespace(
            base_url=settings.chat_base_url.rstrip("/"),
            api_key=SecretStr(settings.chat_api_key),
            chat_model=settings.chat_model,
        )
        shell = type("_VideoChatShell", (), {"model_relay": view})()
        client = httpx.AsyncClient(timeout=90.0)
        return ChatClient(shell, client), True
    return (
        ChatClient(app.state.settings_shell, app.state.model_relay_client._client),
        False,
    )


def _extract_json(text: str) -> dict[str, object] | None:
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


async def generate_summary(
    transcript: str,
    settings: VideoAgentSettings,
    app,
    *,
    title_hint: str = "",
    output_path=None,
) -> dict[str, object]:
    """用 Chat 模型生成摘要并写入 output_dir/summary.json。

    返回 summary dict（{title, summary, keypoints, visual_notes, mode}），失败返回 {}。
    """
    text = (transcript or "").strip()
    if not text:
        return {}
    client, owns = make_chat_client(settings, app)
    try:
        messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT + text[:_TRANSCRIPT_LIMIT]},
            {"role": "user", "content": "请输出摘要 JSON。"},
        ]
        answer = await client.complete(messages)
        data = _extract_json(answer) or {}
        summary: dict[str, object] = {
            "title": str(data.get("title") or "").strip() or title_hint,
            "summary": str(data.get("summary") or "").strip(),
            "keypoints": [
                str(k).strip() for k in (data.get("keypoints") or []) if str(k).strip()
            ],
            "visual_notes": [],
            "mode": "summary",
        }
        if output_path is not None:
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning("write summary.json failed: %s", exc)
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
