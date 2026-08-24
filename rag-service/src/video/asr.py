"""ASR 转写：双渠道（volcengine / dashscope），按 settings.asr_provider 切换。

- volcengine：火山引擎语音技术 · 大模型录音文件极速识别（flash）。
  接口 POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash
  - 一次请求即返回识别结果（无需 submit/query 轮询）。
  - 音频 base64 直传（建议 ≤20MB；我们的分片为 16k mp3，约 1-2MB）。
  - 响应 utterances 自带 begin/end 毫秒时间戳，直接产出 [MM:SS] 句子行。
  凭证（settings.asr_headers）：
  - 新版控制台：X-Api-Key（单一 key）
  - 旧版控制台：X-Api-App-Key + X-Api-Access-Key
  - 固定：X-Api-Resource-Id=volc.bigasr.auc_turbo、X-Api-Request-Id=uuid、X-Api-Sequence=-1

- dashscope：阿里云百炼 · qwen3-asr-flash-filetrans 录音文件识别（异步）。
  接口 POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcriptions
  - 提交 task（X-DashScope-Async: enable）→ 轮询 GET /api/v1/tasks/{id} → 取转写文本。
  - 音频必须为公网可访问 URL：本模块懒启动一个线程化静态文件服务
    （0.0.0.0:ASR_PUBLIC_PORT，默认 18081），把分片临时暴露给百炼拉取，
    转写完成后立即删除。需要：
    - .env 配置 ASR_PUBLIC_HOST=服务器公网 IP
    - 腾讯云安全组放行 ASR_PUBLIC_PORT 入方向
    - docker-compose rag 服务加 ports: ["18081:18081"]
  - 响应 sentences 自带 begin_time/end_time（秒），直接产出 [MM:SS] 句子行。

新增 provider 只需在 asr.py 增加分支 + settings 提供对应凭证/端点字段，
火山与百炼互不影响，后续可随时切换。
"""
from __future__ import annotations

import base64
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from src.video.settings import DASHSCOPE_ASR_PROVIDERS as DASHSCOPE_PROVIDERS
from src.video.transcript import format_time, format_utterance_entries

# ── 渠道常量 ──
# 火山：端点可用 VOLC_ASR_ENDPOINT 覆盖（网关代理 / 测试桩场景）
VOLC_RECOGNIZE_URL = os.environ.get(
    "VOLC_ASR_ENDPOINT",
    "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
)
# 百炼：提交/轮询端点可分别覆盖（网关代理 / 测试桩场景）
DASHSCOPE_SUBMIT_URL = os.environ.get(
    "DASHSCOPE_ASR_ENDPOINT",
    "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
)
DASHSCOPE_TASK_URL = os.environ.get(
    "DASHSCOPE_ASR_TASK_URL",
    "https://dashscope.aliyuncs.com/api/v1/tasks",
)

class AsrError(RuntimeError):
    """ASR 调用失败（含凭证/权限/服务错误）。"""


def _public_dir() -> Path:
    """百炼需要公网 URL 拉取音频；分片先复制到这里对外提供，用完即删。"""
    root = Path(os.environ.get("QUICK_WATCH_TASK_ROOT", "")).expanduser()
    if root.is_absolute():
        return root.parent / "asr_public"
    return Path.cwd() / "data" / "asr_public"


# ── 百炼：内置临时静态文件服务（懒启动，单例） ──

_PUBLIC_SERVER_LOCK = threading.Lock()
_PUBLIC_SERVER_STATE: dict = {"server": None, "dir": None, "port": None}


def _make_static_handler(public_dir: Path):
    """生成只读静态文件 handler：仅 GET 公开目录内的文件，禁止目录列表/穿越。"""
    resolved_root = public_dir.resolve()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                target = (resolved_root / self.path.lstrip("/")).resolve()
                if resolved_root not in target.parents or not target.is_file():
                    self.send_error(404, "Not Found")
                    return
                data = target.read_bytes()
                ctype = (
                    "audio/mpeg"
                    if target.suffix.lower() in (".mp3", ".mp4")
                    else "audio/wav"
                    if target.suffix.lower() == ".wav"
                    else "application/octet-stream"
                )
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except Exception:  # noqa: BLE001
                self.send_error(500, "Internal Error")

        def log_message(self, *args):  # 静默日志
            return

    return _Handler


def _ensure_public_server(public_dir: Path, port: int) -> None:
    """懒启动线程化静态服务；已启动且参数一致则复用。"""
    with _PUBLIC_SERVER_LOCK:
        state = _PUBLIC_SERVER_STATE
        if state["server"] is not None and state["port"] == port and state["dir"] == public_dir:
            return
        if state["server"] is not None:
            try:
                state["server"].shutdown()
            except Exception:  # noqa: BLE001
                pass
        public_dir.mkdir(parents=True, exist_ok=True)
        server = ThreadingHTTPServer(("0.0.0.0", port), _make_static_handler(public_dir))
        thread = threading.Thread(
            target=server.serve_forever, daemon=True, name="asr-public-http"
        )
        thread.start()
        state.update(server=server, dir=public_dir, port=port)


# ── 百炼：filetrans 异步提交 / 轮询 / 取文本 ──

def _dashscope_submit(model: str, file_url: str, api_key: str, endpoint: str) -> str:
    payload = {
        "model": model,
        "input": {"file_url": file_url},
        "parameters": {"channel_id": [0], "enable_itn": False},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
    body = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise AsrError(
            f"百炼提交失败 HTTP {resp.status_code}: "
            f"{body.get('message') or resp.text[:200]}"
        )
    task_id = (body.get("output") or {}).get("task_id")
    if not task_id:
        raise AsrError(f"百炼提交未返回 task_id: {str(body)[:200]}")
    return task_id

def _extract_sentences(data) -> list[dict]:
    """从转写结果 JSON 递归提取 sentences/utterances（兼容多种层级结构）。"""
    def find_list(node, depth=0):
        if depth > 8:
            return None
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            for k in ("sentences", "utterances", "results"):
                v = node.get(k)
                if isinstance(v, list):
                    return v
                r = find_list(v, depth + 1)
                if r is not None:
                    return r
            for v in node.values():
                r = find_list(v, depth + 1)
                if r is not None:
                    return r
        return None

    items = find_list(data) or []
    out = []
    for s in items:
        if isinstance(s, dict) and str(s.get("text") or "").strip():
            out.append(s)
    return out

def _find_url(node, depth=0) -> str | None:
    """递归查找转写结果 URL（兼容 transcription_url / ur_l 两种键）。"""
    if depth > 8:
        return None
    if isinstance(node, dict):
        for k in ("transcription_url", "ur_l"):
            v = node.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in node.values():
            r = _find_url(v, depth + 1)
            if r:
                return r
    return None


def _dashscope_poll_and_fetch(task_id: str, api_key: str, deadline: float) -> list[dict]:
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=30) as client:
        while True:
            if time.perf_counter() >= deadline - 1:
                raise AsrError("ASR 转写超时（百炼轮询）")
            resp = client.get(f"{DASHSCOPE_TASK_URL}/{task_id}", headers=headers)
            body = resp.json() if resp.content else {}
            if resp.status_code != 200:
                raise AsrError(
                    f"百炼查询失败 HTTP {resp.status_code}: "
                    f"{body.get('message') or resp.text[:200]}"
                )
            out = body.get("output") or body
            status = out.get("task_status")
            if status in ("SUCCEEDED", "SUCCESS_WITH_NO_VALID_FRAGMENT"):
                if status == "SUCCESS_WITH_NO_VALID_FRAGMENT":
                    # 空音频/无有效语音：非凭证错误，视为成功（无内容），与火山静音处理一致
                    return []
                url = _find_url(body)
                if not url:
                    raise AsrError("百炼转写成功但响应缺少转录结果 URL")
                tr = client.get(url, headers=headers, timeout=60)
                data = tr.json() if tr.content else {}
                return _extract_sentences(data)
            if status in ("FAILED", "CANCELED"):
                raise AsrError(
                    f"百炼转写失败: {out.get('message') or out.get('code') or status}"
                )
            time.sleep(2.0)

def _normalize_seconds(value: object, max_ms: float = 1000.0) -> float:
    """百炼句子时间戳为秒；若明显为毫秒（值远大于 1000）则换算。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v / 1000.0 if v > max_ms else v


def _sentence_times(s: dict) -> tuple[float, float]:
    """提取句子时间（兼容 begin_time/end_time 秒、start_time/end_time 毫秒）。"""
    begin = s.get("begin_time", s.get("start_time", 0))
    end = s.get("end_time", begin)
    b = _normalize_seconds(begin)
    e = _normalize_seconds(end)
    return b, max(e, b)

def _dashscope_sentences_to_entries(
    sentences: list[dict], chunk_start: float
) -> list[tuple[float, str]]:
    """百炼 sentences → (绝对秒, 文本) 条目（时间戳为秒）。"""
    entries: list[tuple[float, str]] = []
    for s in sentences:
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        begin, _end = _sentence_times(s)
        entries.append((round(chunk_start + begin, 3), text))
    return entries


def _transcribe_chunk_dashscope(
    index: int,
    start: float,
    chunk_path: Path,
    settings,
    request_timeout: float = 120.0,
    retries: int = 1,
) -> dict[str, object]:
    """百炼 qwen3-asr-flash-filetrans：异步提交 + 轮询，分片经内置静态服务暴露。"""
    started = time.perf_counter()
    request_deadline = started + max(1.0, request_timeout)
    error = "unknown error"
    api_calls = 0
    public_file: Path | None = None
    for attempt in range(retries + 1):
        if time.perf_counter() >= request_deadline - 1:
            error = "ASR request budget exhausted"
            break
        try:
            api_key = (settings.asr_api_key or "").strip()
            if not api_key:
                raise AsrError(
                    "ASR 凭证未配置：请在 agent 设置页填写百炼 API Key，"
                    "或配置 .env 的 DASHSCOPE_ASR_API_KEY"
                )
            host = (settings.asr_public_host or "").strip()
            if not host:
                raise AsrError(
                    "未配置公网主机：请在 .env 设置 ASR_PUBLIC_HOST=服务器公网IP，"
                    "并在安全组放行端口"
                )
            port = int(settings.asr_public_port or 18081)
            public_dir = _public_dir()
            _ensure_public_server(public_dir, port)

            public_file = public_dir / f"{uuid.uuid4().hex}{chunk_path.suffix or '.mp3'}"
            public_file.write_bytes(chunk_path.read_bytes())
            file_url = f"http://{host}:{port}/{public_file.name}"

            model = (settings.asr_model or "qwen3-asr-flash-filetrans").strip()
            endpoint = (settings.asr_endpoint or "").strip() or DASHSCOPE_SUBMIT_URL
            api_calls += 1
            task_id = _dashscope_submit(model, file_url, api_key, endpoint)
            sentences = _dashscope_poll_and_fetch(task_id, api_key, request_deadline)

            entries = _dashscope_sentences_to_entries(sentences, start)
            all_text = "\n".join(f"[{format_time(sec)}] {txt}" for sec, txt in entries)
            return {
                "index": index,
                "start": start,
                "text": all_text,
                "raw_utterances": sentences,
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
        finally:
            if public_file is not None:
                try:
                    public_file.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
    return {
        "index": index,
        "start": start,
        "text": "",
        "language": None,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "attempts": api_calls,
        "error": error,
    }


# ── 火山：flash 极速识别（原实现，保持不动） ──

def _transcribe_chunk_volc(
    index: int,
    start: float,
    chunk_path: Path,
    settings,
    request_timeout: float = 120.0,
    retries: int = 1,
) -> dict[str, object]:
    """用火山 flash 极速识别转写一个音频分片。"""
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
                response = client.post(VOLC_RECOGNIZE_URL, json=payload, headers=headers)

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


# ── 双渠道分发入口（保持对外签名不变） ──

def transcribe_chunk(
    index: int,
    start: float,
    chunk_path: Path,
    settings,
    request_timeout: float = 120.0,
    retries: int = 1,
) -> dict[str, object]:
    """按 settings.asr_provider 选择 ASR 渠道转写一个音频分片。"""
    provider = (settings.asr_provider or "volcengine").strip().lower()
    if provider in DASHSCOPE_PROVIDERS:
        return _transcribe_chunk_dashscope(
            index, start, chunk_path, settings, request_timeout, retries
        )
    return _transcribe_chunk_volc(
        index, start, chunk_path, settings, request_timeout, retries
    )


def test_asr_connection(settings, timeout: float = 30.0) -> tuple[bool, str]:
    """验证当前渠道的 ASR 凭证/模型可用；静音音频成功也算连接通过。"""
    provider = (settings.asr_provider or "volcengine").strip().lower()
    if provider in DASHSCOPE_PROVIDERS:
        return _test_asr_connection_dashscope(settings, timeout)
    return _test_asr_connection_volc(settings, timeout)


def _test_asr_connection_volc(settings, timeout: float = 30.0) -> tuple[bool, str]:
    """用一段极短静音音频验证火山 flash 凭证/模型/资源权限可用。"""
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
                VOLC_RECOGNIZE_URL,
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
            )
        status_code = int(response.headers.get("X-Api-Status-Code") or 0)
        if response.status_code == 200 and status_code in (0, 20000000, 20000003, 45000002):
            return True, "连接成功（ASR 凭证与模型可用）"
        body = {}
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            pass
        message = (
            response.headers.get("X-Api-Message")
            or (body.get("header") or {}).get("message")
            or response.text[:200]
        )
        return False, f"连接失败: {message or f'HTTP {response.status_code}'}"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {exc}"


def _test_asr_connection_dashscope(settings, timeout: float = 30.0) -> tuple[bool, str]:
    """下载一段含语音的样例音频 → 公网暴露 → 提交百炼 filetrans → 轮询，验证整链路。

    使用官方 OSS 样例（含语音），能真正识别出句子；空音频/无语音也视为连接通过。
    """
    api_key = (settings.asr_api_key or "").strip()
    if not api_key:
        return False, "ASR 凭证未配置：请在 agent设置页填写百炼 API Key（DASHSCOPE_ASR_API_KEY）"
    host = (settings.asr_public_host or "").strip()
    if not host:
        return False, "未配置公网主机：请在 .env 设置 ASR_PUBLIC_HOST=服务器公网IP，并放行安全组端口"
    port = int(settings.asr_public_port or 18081)
    model = (settings.asr_model or "qwen3-asr-flash-filetrans").strip()
    endpoint = (settings.asr_endpoint or "").strip() or DASHSCOPE_SUBMIT_URL

    sample_url = os.environ.get(
        "DASHSCOPE_ASR_TEST_AUDIO",
        "https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3",
    )
    public_file: Path | None = None
    try:
        with httpx.Client(timeout=min(30.0, timeout)) as client:
            resp = client.get(sample_url)
            if resp.status_code != 200:
                return False, f"测试音频下载失败 HTTP {resp.status_code}"
            audio_bytes = resp.content
        public_dir = _public_dir()
        _ensure_public_server(public_dir, port)
        public_file = public_dir / f"asr_test_{uuid.uuid4().hex}.mp3"
        public_file.write_bytes(audio_bytes)
        file_url = f"http://{host}:{port}/{public_file.name}"
        deadline = time.perf_counter() + max(5.0, timeout)
        task_id = _dashscope_submit(model, file_url, api_key, endpoint)
        sentences = _dashscope_poll_and_fetch(task_id, api_key, deadline)
        if sentences:
            sample = str(sentences[0].get("text") or "")[:40]
            return True, f"连接成功（百炼可用，识别 {len(sentences)} 句，首句: {sample}）"
        return True, "连接成功（百炼可用，测试音频未含可识别语音）"
    except AsrError as exc:
        return False, f"连接失败: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {exc}"
    finally:
        if public_file is not None:
            try:
                public_file.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
