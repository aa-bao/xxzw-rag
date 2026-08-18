"""ASR 转写：火山引擎语音技术 · 大模型录音文件极速识别（flash）。

接口：POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash
- 一次请求即返回识别结果（无需 submit/query 轮询）。
- 音频 base64 直传（建议 ≤20MB；我们的分片为 16k mp3，约 1-2MB）。
- 响应 utterances 自带 begin/end 毫秒时间戳，直接产出 [MM:SS] 句子行。

凭证（settings.asr_headers）：
- 新版控制台：X-Api-Key（单一 key）
- 旧版控制台：X-Api-App-Key + X-Api-Access-Key
- 固定：X-Api-Resource-Id=volc.bigasr.auc_turbo、X-Api-Request-Id=uuid、X-Api-Sequence=-1
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import httpx

from src.video.transcript import format_time, format_utterance_entries

# 端点可用 VOLC_ASR_ENDPOINT 覆盖（网关代理 / 测试桩场景）
RECOGNIZE_URL = os.environ.get(
    "VOLC_ASR_ENDPOINT",
    "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
)


class AsrError(RuntimeError):
    """ASR 调用失败（含凭证/权限/服务错误）。"""


def transcribe_chunk(
    index: int,
    start: float,
    chunk_path: Path,
    settings,  # VideoAgentSettings
    request_timeout: float = 120.0,
    retries: int = 1,
) -> dict[str, object]:
    """用火山 flash 极速识别转写一个音频分片。

    返回 {index, start, text（带 [MM:SS] 时间戳行）, raw_utterances,
           elapsed_seconds, attempts, error}。
    """
    started = time.perf_counter()
    request_deadline = started + max(1.0, request_timeout)
    error = "unknown error"
    api_calls = 0
    for attempt in range(retries + 1):
        request_remaining = request_deadline - time.perf_counter()
        if request_remaining <= 1:
            error = "ASR request budget exhausted"
            break
        try:
            api_calls += 1
            headers = settings.asr_headers
            if headers is None:
                raise AsrError(
                    "ASR 凭证未配置：请在 agent设置页填写火山引擎语音技术 "
                    "API Key（或 App ID + Access Token），或配置 .env 的 VOLC_ASR_*"
                )
            headers = {**headers, "Content-Type": "application/json"}

            payload: dict[str, object] = {
                "user": {"uid": settings.asr_api_key or settings.asr_app_id or "rag-video"},
                "audio": {
                    "data": base64.b64encode(chunk_path.read_bytes()).decode("ascii")
                },
                "request": {"model_name": settings.asr_model or "bigmodel"},
            }

            with httpx.Client(timeout=max(5.0, request_remaining)) as client:
                response = client.post(RECOGNIZE_URL, json=payload, headers=headers)

            status_code = int(response.headers.get("X-Api-Status-Code") or 0)
            body = response.json() if response.content else {}
            header_code = int(((body.get("header") or {}).get("code")) or 0)

            if response.status_code != 200:
                raise AsrError(
                    f"ASR HTTP {response.status_code}: "
                    f"{body.get('header', {}).get('message') or response.text[:300]}"
                )
            if status_code and status_code != 20000000:
                message = response.headers.get("X-Api-Message") or body.get("header", {}).get("message") or ""
                if status_code in (20000003, 45000002):
                    # 静音/空音频：不是凭证错误，视为成功（无内容）
                    result = {"text": "", "utterances": []}
                else:
                    raise AsrError(f"ASR 失败({status_code}): {message}")
            elif header_code and header_code not in (0, 20000000):
                if header_code in (20000003, 45000002):
                    result = {"text": "", "utterances": []}
                else:
                    raise AsrError(
                        f"ASR 失败({header_code}): {body.get('header', {}).get('message')}"
                    )
            else:
                result = body.get("result") or {}

            utterances = list(result.get("utterances") or [])
            entries = format_utterance_entries(utterances, chunk_start=start)
            all_text = "\n".join(f"[{format_time(sec)}] {txt}" for sec, txt in entries)

            return {
                "index": index,
                "start": start,
                "text": all_text,
                "raw_utterances": utterances,
                "language": None,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "attempts": api_calls,
                "error": None,
            }
        except AsrError as exc:
            error = str(exc)
            # 凭证/权限类错误不重试
            if any(k in error for k in ("401", "403", "未配置", "没有权限", "InvalidParameter")):
                break
            if attempt < retries:
                time.sleep(min(0.5 * (attempt + 1), max(0, request_deadline - time.perf_counter())))
        except Exception as exc:  # noqa: BLE001 — 网络/超时等，统一进入重试
            error = str(exc) or "ASR request failed"
            if attempt < retries:
                time.sleep(min(0.5 * (attempt + 1), max(0, request_deadline - time.perf_counter())))
    return {
        "index": index,
        "start": start,
        "text": "",
        "language": None,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "attempts": api_calls,
        "error": error,
    }


def test_asr_connection(settings, timeout: float = 30.0) -> tuple[bool, str]:
    """用一段极短静音音频验证 ASR 凭证/模型/资源权限可用。

    返回 (ok, message)。静音音频返回 20000003 也算连接成功（鉴权通过）。
    """
    import wave

    headers = settings.asr_headers
    if headers is None:
        return False, "ASR 凭证未配置：请在 agent设置页填写 API Key 或 App ID + Access Token"
    # 生成 0.3s 静音 wav（16k 单声道 16bit）
    import io
    import math
    import struct

    sr = 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(
            struct.pack("<h", int(1200 * math.sin(2 * math.pi * 440 * i / sr)))
            for i in range(int(sr * 0.3))
        ))
    try:
        payload: dict[str, object] = {
            "user": {"uid": settings.asr_api_key or settings.asr_app_id or "rag-video"},
            "audio": {"data": base64.b64encode(buf.getvalue()).decode("ascii")},
            "request": {"model_name": settings.asr_model or "bigmodel"},
        }
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                RECOGNIZE_URL,
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
            )
        status_code = int(response.headers.get("X-Api-Status-Code") or 0)
        if response.status_code == 200 and status_code in (0, 20000000, 20000003, 45000002):
            return True, "连接成功（ASR 凭证与模型可用）"
        body = {}
        try:
            body = response.json()
        except Exception:
            pass
        message = (
            response.headers.get("X-Api-Message")
            or (body.get("header") or {}).get("message")
            or response.text[:200]
        )
        return False, f"连接失败: {message or f'HTTP {response.status_code}'}"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {exc}"
