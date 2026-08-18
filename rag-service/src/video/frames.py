"""关键帧提取：ffmpeg 场景检测 + 去重 + 均匀采样。

移植自 quick-watch/scripts/frames.py 的 extract_keyframes 核心链路
（ffmpeg 由 imageio-ffmpeg 提供；ffprobe 缺失时用 PyAV 探测元数据）。
同步阻塞实现，由 service.py 通过 asyncio.to_thread 调度。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

# 常量（与 quick-watch frames.py 一致）
MAX_FPS = 2.0
SCENE_THRESHOLD = 0.02
SCENE_MIN_FRAMES = 8
KEYFRAME_MIN = 4
MAX_READ_DIMENSION = 1998
DEDUP_THUMB = 16
DEDUP_THRESHOLD = 2.0
SHOWINFO_TS_RE = re.compile(r"pts_time:([0-9.]+)")


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _ffmpeg_available() -> bool:
    return _ffmpeg() is not None


def _safe_unlink(path) -> None:
    """删除文件，沙箱拒绝时不中断提取。"""
    try:
        Path(path).unlink()
    except (SystemExit, FileNotFoundError, OSError):
        pass


def _scale_filter(resolution: int) -> str:
    return (
        f"scale=w='min({resolution},iw)':h='min({MAX_READ_DIMENSION},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _clamp_fps(fps: float, duration_seconds: float, max_frames: int) -> tuple[float, int]:
    fps = min(fps, MAX_FPS)
    target = min(max_frames, max(1, int(round(fps * duration_seconds))))
    return fps, target


def auto_fps(duration_seconds: float, max_frames: int = 100) -> tuple[float, int]:
    """按时长预算抽帧率（短视频密集、长视频封顶）。"""
    if duration_seconds <= 0:
        return 1.0, 1
    if duration_seconds <= 30:
        target = min(max_frames, max(12, int(round(duration_seconds))))
    elif duration_seconds <= 60:
        target = min(max_frames, 40)
    elif duration_seconds <= 180:  # 3 min
        target = min(max_frames, 60)
    elif duration_seconds <= 600:  # 10 min
        target = min(max_frames, 80)
    else:
        target = max_frames
    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


def get_metadata(video_path: str) -> dict:
    """探测视频元数据；ffprobe 缺失时用 PyAV 兜底。"""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(Path(video_path).resolve()),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams", [])
            fmt = data.get("format", {})
            video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
            audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            duration = float(fmt.get("duration") or video_stream.get("duration") or 0)
            return {
                "duration_seconds": duration,
                "width": video_stream.get("width"),
                "height": video_stream.get("height"),
                "codec": video_stream.get("codec_name"),
                "size_bytes": int(fmt.get("size") or 0),
                "has_audio": audio_stream is not None,
            }
    try:
        import av

        with av.open(video_path) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            duration = float(container.duration / av.time_base) if container.duration else 0.0
            return {
                "duration_seconds": duration,
                "width": stream.width if stream else None,
                "height": stream.height if stream else None,
                "codec": stream.codec_context.name if stream else None,
                "size_bytes": Path(video_path).stat().st_size,
                "has_audio": any(s.type == "audio" for s in container.streams),
            }
    except Exception:
        return {"duration_seconds": 0.0}


def extract(
    video_path: str,
    out_dir: Path,
    fps: float,
    resolution: int = 512,
    max_frames: int = 100,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[dict]:
    """按均匀帧率抽帧（showinfo 读真实 PTS）。"""
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob("frame_*.jpg"):
        _safe_unlink(existing)
    output_pattern = str(out_dir / "frame_%04d.jpg")
    cmd: list[str] = [_ffmpeg(), "-hide_banner", "-loglevel", "info", "-y"]
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]
    cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", f"fps={fps},{_scale_filter(resolution)},showinfo",
        "-frames:v", str(max_frames),
        "-q:v", "4",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr.strip()}")
    offset = start_seconds or 0.0
    timestamps = [round(offset + float(m.group(1)), 2) for m in SHOWINFO_TS_RE.finditer(result.stderr)]
    frames = sorted(out_dir.glob("frame_*.jpg"))
    out: list[dict] = []
    for i, path in enumerate(frames):
        ts = timestamps[i] if i < len(timestamps) else offset
        out.append({
            "index": i,
            "timestamp_seconds": ts,
            "path": str(path),
            "reason": "uniform",
        })
    return out


def extract_scene_candidates(
    video_path: str,
    out_dir: Path,
    resolution: int = 512,
    max_frames: int | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    threshold: float = SCENE_THRESHOLD,
) -> list[dict]:
    """提取首帧 + ffmpeg 场景切换帧（全范围检测，供去重/采样）。"""
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob("frame_*.jpg"):
        _safe_unlink(existing)
    output_pattern = str(out_dir / "frame_%04d.jpg")
    cmd: list[str] = [_ffmpeg(), "-hide_banner", "-loglevel", "info", "-y"]
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]
    vf = f"select='eq(n\\,0)+gt(scene\\,{threshold})',{_scale_filter(resolution)},showinfo"
    cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", vf,
        "-vsync", "vfr",
    ]
    if max_frames is not None:
        cmd += ["-frames:v", str(max_frames)]
    cmd += ["-q:v", "4", output_pattern]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg scene extraction failed: {result.stderr.strip()}")
    offset = start_seconds or 0.0
    timestamps = [round(offset + float(m.group(1)), 2) for m in SHOWINFO_TS_RE.finditer(result.stderr)]
    frames = sorted(out_dir.glob("frame_*.jpg"))
    out: list[dict] = []
    for i, path in enumerate(frames):
        ts = timestamps[i] if i < len(timestamps) else offset
        out.append({
            "index": i,
            "timestamp_seconds": ts,
            "path": str(path),
            "reason": "first-frame" if i == 0 else "scene-change",
        })
    return out


def _even_indices(count: int, n: int) -> list[int]:
    if n >= count:
        return list(range(count))
    if n <= 1:
        return [0]
    return [round(i * (count - 1) / (n - 1)) for i in range(n)]


def _even_sample(candidates: list[dict], n: int) -> list[dict]:
    """均匀采样（首尾必留），删除落选帧并重排 index。"""
    selected = [candidates[i] for i in _even_indices(len(candidates), n)]
    keep_paths = {sel["path"] for sel in selected}
    for cand in candidates:
        if cand["path"] not in keep_paths:
            _safe_unlink(Path(cand["path"]))
    for i, frame in enumerate(selected):
        frame["index"] = i
    return selected


def _frame_delta(a: bytes, b: bytes) -> float:
    if not a or len(a) != len(b):
        return float("inf")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _thumb_frames(paths: list[Path]) -> list[bytes]:
    """一次 ffmpeg 把全部帧解码为 DEDUP_THUMB 方形灰度缩略图。"""
    if not paths:
        return []
    paths = [Path(p) for p in paths]
    m = re.match(r"(.*?)(\d+)(\.[A-Za-z0-9]+)$", paths[0].name)
    if m is None:
        return []
    prefix, digits, ext = m.group(1), m.group(2), m.group(3)
    pattern = str(paths[0].parent / f"{prefix}%0{len(digits)}d{ext}")
    cmd = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel", "error",
        "-start_number", str(int(digits)),
        "-i", pattern,
        "-vf", f"scale={DEDUP_THUMB}:{DEDUP_THUMB},format=gray",
        "-f", "rawvideo",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return []
    chunk = DEDUP_THUMB * DEDUP_THUMB
    data = result.stdout
    if len(data) != chunk * len(paths):
        return []
    return [data[i * chunk:(i + 1) * chunk] for i in range(len(paths))]


def dedupe_perceptual(
    candidates: list[dict], threshold: float = DEDUP_THRESHOLD
) -> tuple[list[dict], int]:
    """按均值像素差去除近重复帧；返回 (幸存者, 删除数)。"""
    if len(candidates) <= 1:
        return candidates, 0
    thumbs = _thumb_frames([Path(c["path"]) for c in candidates])
    if len(thumbs) != len(candidates):
        return candidates, 0
    kept = [candidates[0]]
    last = thumbs[0]
    dropped: list[dict] = []
    for cand, thumb in zip(candidates[1:], thumbs[1:]):
        if _frame_delta(thumb, last) <= threshold:
            dropped.append(cand)
        else:
            kept.append(cand)
            last = thumb
    for cand in dropped:
        _safe_unlink(Path(cand["path"]))
    for i, frame in enumerate(kept):
        frame["index"] = i
    return kept, len(dropped)


def extract_keyframes(
    video_path: str,
    out_dir: Path,
    resolution: int = 512,
    max_frames: int | None = 50,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    dedup: bool = True,
) -> tuple[list[dict], dict]:
    """提取关键帧：场景检测优先，静态视频回退均匀采样，最后去重+均匀裁剪。

    返回 (frames, meta)。frames 元素含 timestamp_seconds / path。
    """
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob("frame_*.jpg"):
        _safe_unlink(existing)

    if max_frames is not None:
        # 有界模式：全范围场景检测（不封顶），然后去重 + 均匀裁剪到预算
        scene_frames = extract_scene_candidates(
            video_path, out_dir,
            resolution=resolution,
            max_frames=None,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        scene_count = len(scene_frames)
        n_dropped = 0
        if scene_count >= SCENE_MIN_FRAMES:
            if dedup:
                scene_frames, n_dropped = dedupe_perceptual(scene_frames)
            selected = _even_sample(scene_frames, max_frames)
            return selected, {
                "engine": "scene",
                "candidate_count": scene_count,
                "deduped_count": n_dropped,
                "selected_count": len(selected),
                "fallback": False,
            }
        # 静态视频 → 均匀采样
        full_duration = get_metadata(video_path).get("duration_seconds") or 0.0
        eff_start = start_seconds or 0.0
        eff_end = end_seconds if end_seconds is not None else full_duration
        eff_duration = max(0.0, eff_end - eff_start)
        fps, _ = auto_fps(eff_duration, max_frames=max_frames)
        candidates = extract(
            video_path, out_dir,
            fps=fps, resolution=resolution,
            max_frames=max_frames * 3,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        n_dropped = 0
        if dedup:
            candidates, n_dropped = dedupe_perceptual(candidates)
        selected = _even_sample(candidates, max_frames)
        return selected, {
            "engine": "uniform",
            "candidate_count": len(candidates) + n_dropped,
            "deduped_count": n_dropped,
            "selected_count": len(selected),
            "fallback": True,
        }

    # 无界模式：抽关键帧（I 帧），不足则回退均匀
    output_pattern = str(out_dir / "frame_%04d.jpg")
    cmd: list[str] = [_ffmpeg(), "-hide_banner", "-loglevel", "info", "-y"]
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]
    cmd += [
        "-skip_frame", "nokey",
        "-i", str(Path(video_path).resolve()),
        "-vf", f"{_scale_filter(resolution)},showinfo",
        "-vsync", "vfr",
        "-q:v", "4",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg keyframe extraction failed: {result.stderr.strip()}")
    offset = start_seconds or 0.0
    timestamps = [round(offset + float(m.group(1)), 2) for m in SHOWINFO_TS_RE.finditer(result.stderr)]
    files = sorted(out_dir.glob("frame_*.jpg"))
    candidates: list[dict] = []
    for i, path in enumerate(files):
        ts = timestamps[i] if i < len(timestamps) else offset
        candidates.append({
            "index": i,
            "timestamp_seconds": ts,
            "path": str(path),
            "reason": "keyframe",
        })
    if len(candidates) < KEYFRAME_MIN:
        for cand in candidates:
            _safe_unlink(Path(cand["path"]))
        full_duration = get_metadata(video_path).get("duration_seconds") or 0.0
        eff_start = start_seconds or 0.0
        eff_end = end_seconds if end_seconds is not None else full_duration
        eff_duration = max(0.0, eff_end - eff_start)
        budget = max_frames if max_frames is not None else 100
        fps, _ = auto_fps(eff_duration, max_frames=budget)
        frames_out = extract(
            video_path, out_dir,
            fps=fps, resolution=resolution, max_frames=budget,
            start_seconds=start_seconds, end_seconds=end_seconds,
        )
        n_dropped = 0
        if dedup:
            frames_out, n_dropped = dedupe_perceptual(frames_out)
        return frames_out, {
            "engine": "uniform",
            "candidate_count": len(candidates),
            "deduped_count": n_dropped,
            "selected_count": len(frames_out),
            "fallback": True,
        }
    candidate_count = len(candidates)
    deduped, n_dropped = dedupe_perceptual(candidates) if dedup else (candidates, 0)
    cap = len(deduped) if max_frames is None else max_frames
    selected = _even_sample(deduped, cap)
    return selected, {
        "engine": "keyframe",
        "candidate_count": candidate_count,
        "deduped_count": n_dropped,
        "selected_count": len(selected),
        "fallback": False,
    }
