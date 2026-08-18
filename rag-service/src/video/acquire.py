"""视频/字幕获取：URL 校验、微信视频号解析、yt-dlp 字幕与音频下载、本地音频提取。

移植自 quick-watch（去除 CLI/锁/进度通知/Playwright 浏览器兜底——首版仅保留
yt-dlp 路径 + 微信解析 + 本地浏览器 cookie 兜底，浏览器兜底标 TODO）。

所有函数为同步阻塞实现（subprocess/网络），由 service.py 通过
asyncio.to_thread 调度，避免阻塞事件循环。
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

# 微信视频号分享链接解析服务
SPH_API_ENDPOINT = "https://sph.litao.workers.dev/api/fetch_video_profile"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class AcquisitionError(RuntimeError):
    """获取失败（提示用户改用上传本地文件）。"""


class DependencyError(RuntimeError):
    """运行时工具缺失（yt-dlp / ffmpeg 未安装）。"""


# ── URL 校验 ──

def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_public_url(source: str) -> None:
    parsed = urlparse(source)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("a public HTTP(S) URL is required")
    if hostname.lower() == "localhost":
        raise ValueError("private-network URLs are not allowed")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            }
        except OSError as exc:
            raise ValueError(f"URL hostname could not be resolved: {hostname}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("private-network URLs are not allowed")


def failure_status(error: Exception) -> str:
    return "needs_upload" if isinstance(error, AcquisitionError) else "failed"


# ── 微信视频号 ──

def is_weixin_sph_url(source: str) -> bool:
    parsed = urlparse(source)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower() == "weixin.qq.com"
        and parsed.path.startswith("/sph/")
    )


def weixin_sph_id(source: str) -> str | None:
    path = urlparse(source).path
    match = re.match(r"^/sph/([^/]+)", path)
    return match.group(1) if match else None


def resolve_weixin_source(source: str, timeout: float) -> dict[str, object]:
    """把微信视频号分享链接换成可直接下载的视频 URL。

    调用提取服务的 fetch_video_profile API（要求浏览器 User-Agent），返回
    带签名的 finder.video.qq.com 直链以及作者/标题元数据。
    """
    import urllib.request

    request = urllib.request.Request(
        SPH_API_ENDPOINT,
        data=json.dumps({"url": source}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, timeout)) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"视频号解析失败: {exc}") from exc
    feed = ((body.get("data") or {}).get("feedInfo") or {})
    author = ((body.get("data") or {}).get("authorInfo") or {})
    direct_url = (
        feed.get("videoUrl")
        or (feed.get("h264VideoInfo") or {}).get("videoUrl")
        or ""
    )
    if not direct_url:
        raise RuntimeError("视频号解析结果中未找到视频链接")
    validate_public_url(direct_url)
    return {
        "url": direct_url,
        "title": feed.get("description") or "",
        "uploader": author.get("nickname") or "",
        "cover_url": feed.get("coverUrl") or "",
    }


# ── 来源规范化 ──

def normalize_url(u: str) -> str:
    p = urlparse(u)
    scheme = p.scheme.lower()
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path
    if path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = ""
    if p.query:
        pairs = sorted(parse_qsl(p.query, keep_blank_values=True), key=lambda kv: kv[0])
        query = "?" + urlencode(pairs)
    return f"{scheme}://{netloc}{path}{query}"


def _video_id(u: str) -> str | None:
    p = urlparse(u)
    netloc = p.netloc.lower()
    if "bilibili" in netloc:
        match = re.search(r"BV[0-9A-Za-z]+", u)
        if match:
            return match.group(0)
    if netloc == "weixin.qq.com":
        return weixin_sph_id(u)
    match = re.search(r"[?&]v=([^&]+)", u)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([^?/]+)", u)
    if match:
        return match.group(1)
    return None


def source_fingerprint(source: str) -> str:
    if is_url(source):
        vid = _video_id(source)
        identity = f"url\0{vid}" if vid else f"url\0{normalize_url(source)}"
    else:
        path = Path(source).expanduser().resolve()
        stat = path.stat()
        identity = f"file\0{path}\0{stat.st_size}\0{stat.st_mtime_ns}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


# ── ffmpeg / 工具 ──

def ffmpeg_executable() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_command(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=max(1.0, timeout),
    )


def run_ffmpeg(args: list[str], timeout: float) -> None:
    result = run_command(
        [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y", *args],
        timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFmpeg failed")


def media_duration(path: Path) -> float:
    import av

    with av.open(str(path)) as container:
        if container.duration is None:
            raise RuntimeError(f"Duration is unavailable: {path}")
        return float(container.duration / av.time_base)


def extract_local_audio(source: Path, destination: Path, timeout: float) -> None:
    """ffmpeg 提取 16k 单声道 mp3。"""
    run_ffmpeg(
        [
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(destination),
        ],
        timeout,
    )


# ── VTT 字幕解析 ──

def _caption_delta(previous: str, current: str) -> str:
    if current == previous:
        return ""
    if previous and current.startswith(previous):
        return current[len(previous):].strip()
    return current


def parse_vtt_text(vtt: str) -> str:
    blocks = re.split(r"\r?\n\s*\r?\n", vtt.strip())
    output: list[str] = []
    previous = ""
    timing = re.compile(r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->")
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        match = timing.search(lines[time_index])
        if not match:
            continue
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        start = hours * 3600 + minutes * 60 + seconds
        raw_text = " ".join(lines[time_index + 1:])
        clean = html.unescape(re.sub(r"<[^>]+>", "", raw_text)).strip()
        clean = re.sub(r"\s+", " ", clean)
        delta = _caption_delta(previous, clean)
        if delta:
            from src.video.transcript import format_time
            output.append(f"[{format_time(start)}] {delta}")
        previous = clean
    return "\n".join(output)


def _vtt_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) >= 3 else 0
    return hours * 3600 + minutes * 60 + seconds


def parse_vtt_cues(vtt: str) -> list[dict[str, object]]:
    cues: list[dict[str, object]] = []
    previous = ""
    timing = re.compile(
        r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
        r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
    )
    for block in re.split(r"\r?\n\s*\r?\n", vtt.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        match = timing.search(lines[time_index])
        if not match:
            continue
        raw_text = " ".join(lines[time_index + 1:])
        clean = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", raw_text))).strip()
        delta = _caption_delta(previous, clean)
        if delta:
            cues.append(
                {
                    "start": round(_vtt_seconds(match.group("start")), 3),
                    "end": round(_vtt_seconds(match.group("end")), 3),
                    "text": delta,
                }
            )
        previous = clean
    return cues


# ── 字幕覆盖率 ──

def caption_coverage(cues: list[dict[str, object]], duration: float) -> dict[str, object]:
    """统计有字幕覆盖的时间，并返回未覆盖的时间段。"""
    if duration <= 0:
        return {"ratio": 0.0, "gaps": []}
    intervals: list[tuple[float, float]] = []
    for cue in cues:
        start = max(0.0, float(cue.get("start", 0)))
        end = min(duration, float(cue.get("end", 0)))
        if end > start:
            intervals.append((start, end))
    intervals.sort()
    merged: list[list[float]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            gaps.append((round(cursor, 3), round(start, 3)))
        cursor = max(cursor, end)
    if cursor < duration:
        gaps.append((round(cursor, 3), round(duration, 3)))
    covered = sum(end - start for start, end in merged)
    return {"ratio": round(covered / duration, 6), "gaps": gaps}


def select_asr_ranges(
    gaps: list[tuple[float, float]], minimum_seconds: float = 8.0
) -> list[tuple[float, float]]:
    return [(start, end) for start, end in gaps if end - start >= minimum_seconds]


def caption_needs_asr(coverage: dict[str, object], minimum_ratio: float) -> bool:
    return float(coverage["ratio"]) < minimum_ratio or bool(
        select_asr_ranges(list(coverage["gaps"]))
    )


def choose_asr_ranges(
    coverage: dict[str, object], duration: float, minimum_ratio: float
) -> list[tuple[float, float]]:
    if float(coverage["ratio"]) < minimum_ratio:
        return [(0.0, duration)]
    return select_asr_ranges(list(coverage["gaps"]))


def select_best_caption_candidate(
    candidates: list[dict[str, object]], duration: float
) -> dict[str, object]:
    return max(
        candidates,
        key=lambda item: (
            float(caption_coverage(list(item.get("cues") or []), duration)["ratio"]),
            float(item.get("language_bonus") or 0),
            len(str(item.get("transcript") or "")),
        ),
        default={"transcript": "", "cues": []},
    )


# ── yt-dlp 字幕 ──

def try_url_captions(source: str, out_dir: Path, timeout: float) -> dict[str, object]:
    """用 yt-dlp 抓取字幕（zh.* / en.*，vtt 格式），统计覆盖率。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    template = out_dir / "source.%(ext)s"
    result = run_command(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--skip-download",
            "--no-playlist",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "zh.*,en.*",
            "--sub-format",
            "vtt",
            "--write-info-json",
            "--no-warnings",
            "-o",
            str(template),
            source,
        ],
        timeout,
    )
    info_files = list(out_dir.glob("*.info.json"))
    info = json.loads(info_files[0].read_text(encoding="utf-8")) if info_files else {}
    duration = float(info.get("duration") or 0)
    candidates: list[dict[str, object]] = []
    for subtitle in sorted(out_dir.glob("*.vtt")):
        vtt = subtitle.read_text(encoding="utf-8", errors="replace")
        transcript = parse_vtt_text(vtt)
        if transcript.strip():
            cues = parse_vtt_cues(vtt)
            ratio = float(caption_coverage(cues, duration)["ratio"]) if duration else 0
            language_bonus = 0.01 if re.search(r"(?:^|\.)zh(?:[.-]|\.)", subtitle.name, re.I) else 0
            candidates.append(
                {
                    "transcript": transcript,
                    "cues": cues,
                    "score": ratio + language_bonus,
                }
            )
    if candidates:
        selected = max(
            candidates,
            key=lambda item: (float(item["score"]), len(str(item["transcript"]))),
        )
        return {
            "transcript": selected["transcript"],
            "cues": selected["cues"],
            "duration": duration,
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "error": None,
        }
    return {
        "transcript": "",
        "cues": [],
        "duration": duration,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "error": result.stderr.strip() if result.returncode else None,
    }


# ── yt-dlp 音频/视频下载 ──

def download_url_audio(
    source: str,
    out_dir: Path,
    timeout: float,
    referer: str | None = None,
    cookies: Path | None = None,
    cookies_from_browser: str | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    template = out_dir / "audio.%(ext)s"
    command = [sys.executable, "-m", "yt_dlp"]
    if cookies:
        command.extend(["--cookies", str(cookies)])
    elif cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])
    if referer:
        command.extend(["--add-header", f"Referer:{referer}"])
    command.extend(
        [
            "--no-playlist",
            "-f",
            "ba/bestaudio/b",
            "--ffmpeg-location",
            ffmpeg_executable(),
            "--no-warnings",
            "-o",
            str(template),
            source,
        ]
    )
    result = run_command(command, timeout)
    candidates = sorted(out_dir.glob("audio.*"))
    if result.returncode != 0 or not candidates:
        if "No module named yt_dlp" in result.stderr:
            raise DependencyError("yt-dlp is not installed")
        raise RuntimeError(result.stderr.strip() or "yt-dlp produced no audio")
    return candidates[0]


def download_url_video(
    source: str,
    out_dir: Path,
    timeout: float,
    referer: str | None = None,
    cookies: Path | None = None,
    cookies_from_browser: str | None = None,
    max_height: int = 360,
) -> Path:
    """下载低码率视频（用于关键帧提取）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "yt_dlp"]
    if cookies:
        command.extend(["--cookies", str(cookies)])
    elif cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])
    if referer:
        command.extend(["--add-header", f"Referer:{referer}"])
    command.extend(
        [
            "--no-playlist",
            "-f",
            f"bestvideo[height<={max_height}]+bestaudio/best",
            "--ffmpeg-location",
            ffmpeg_executable(),
            "--no-warnings",
            "-o",
            str(out_dir / "video.%(ext)s"),
            source,
        ]
    )
    result = run_command(command, timeout)
    candidates = sorted(out_dir.glob("video.*"))
    if result.returncode != 0 or not candidates:
        if "No module named yt_dlp" in result.stderr:
            raise DependencyError("yt-dlp is not installed")
        raise RuntimeError(result.stderr.strip() or "yt-dlp produced no video")
    return candidates[0]


# ── 音频获取总入口 ──

def acquire_url_audio(
    source: str,
    work: Path,
    timeout: float,
    cookie_file: Path | None = None,
    referer: str | None = None,
    resolve_direct: object | None = None,
) -> tuple[Path, str, list[dict[str, object]] | None, Path | None, str | None]:
    """从公开 URL 下载音频。

    返回 (audio_path, acquisition_source, browser_cues, browser_cookies, browser_referer)。

    主路径 yt-dlp；失败时若提供 resolve_direct（微信视频号重新解析）则重试；
    若错误信息含 cookie 则尝试本地浏览器 cookie（yt-dlp --cookies-from-browser）。
    Playwright 浏览器兜底暂未移植（TODO：有 cookies 文件优先）。
    """
    import time

    started = time.perf_counter()

    def time_left() -> float:
        return max(1.0, timeout - (time.perf_counter() - started))

    try:
        return (
            download_url_audio(
                source,
                work / "download",
                time_left(),
                referer=referer,
                cookies=cookie_file,
            ),
            "yt-dlp", None, None, None,
        )
    except DependencyError:
        raise
    except ModuleNotFoundError as exc:
        raise DependencyError(str(exc)) from exc
    except Exception as primary_error:
        if resolve_direct is not None:
            try:
                return (
                    download_url_audio(
                        str(resolve_direct()),
                        work / "resolved-download",
                        time_left(),
                        referer=referer,
                        cookies=cookie_file,
                    ),
                    "yt-dlp-resolved", None, None, None,
                )
            except Exception:
                pass
        # 抖音/B站/小红书往往只需要一个来自真实浏览器的 *新鲜* 会话 cookie。
        if "cookie" in str(primary_error).lower():
            for br in ("chrome", "edge", "chromium", "firefox"):
                try:
                    return (
                        download_url_audio(
                            source, work / "cookie-download", time_left(),
                            cookies_from_browser=br,
                        ),
                        "yt-dlp-cookies", None, None, None,
                    )
                except Exception:
                    continue
        raise AcquisitionError(str(primary_error)) from primary_error
