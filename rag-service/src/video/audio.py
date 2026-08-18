"""音频处理：静音检测、VAD 分片规划、按计划切分 16k mp3 分片。

移植自 quick-watch（去除 CLI/锁/进度通知等外围）。同步阻塞实现，由
service.py 通过 asyncio.to_thread 调度。
"""
from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

from src.video.acquire import ffmpeg_executable, run_command, run_ffmpeg


def parse_silence_points(stderr: str) -> list[float]:
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]
    return [round((start + end) / 2, 3) for start, end in zip(starts, ends) if end >= start]


def detect_silence_points(audio_path: Path, timeout: float) -> list[float]:
    """ffmpeg silencedetect：-35dB / 0.45s 的静音点（VAD 分片依据）。"""
    null_output = "NUL" if os.name == "nt" else "/dev/null"
    result = run_command(
        [
            ffmpeg_executable(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio_path),
            "-af",
            "silencedetect=n=-35dB:d=0.45",
            "-f",
            "null",
            null_output,
        ],
        timeout,
    )
    return parse_silence_points(result.stderr)


def plan_vad_chunks(
    duration: float,
    silence_points: list[float],
    target_seconds: int = 150,
    min_seconds: int = 120,
    max_seconds: int = 180,
    overlap_seconds: float = 1.5,
) -> list[tuple[int, float, float]]:
    """规划重叠的分片，优先选择靠近每个目标边界的静音点。"""
    if duration <= 0:
        return []
    boundaries = [0.0]
    silences = sorted(point for point in silence_points if 0 < point < duration)
    while duration - boundaries[-1] > max_seconds:
        start = boundaries[-1]
        candidates = [
            point for point in silences if start + min_seconds <= point <= start + max_seconds
        ]
        boundary = (
            min(candidates, key=lambda point: abs(point - (start + target_seconds)))
            if candidates
            else min(start + target_seconds, duration)
        )
        if boundary <= start:
            break
        boundaries.append(round(boundary, 3))
    boundaries.append(float(duration))
    return [
        (
            index,
            round(max(0.0, start - (overlap_seconds if index else 0.0)), 3),
            round(
                min(duration, end + (overlap_seconds if index < len(boundaries) - 2 else 0.0)),
                3,
            ),
        )
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]))
    ]


def plan_asr_chunks(
    duration: float,
    silence_points: list[float],
    ranges: list[tuple[float, float]] | None = None,
) -> list[tuple[int, float, float]]:
    selected_ranges = [(0.0, duration)] if ranges is None else ranges
    output: list[tuple[int, float, float]] = []
    for range_start, range_end in selected_ranges:
        relative_silences = [
            point - range_start for point in silence_points if range_start < point < range_end
        ]
        local = plan_vad_chunks(range_end - range_start, relative_silences)
        for _local_index, start, end in local:
            output.append(
                (
                    len(output),
                    round(range_start + start, 3),
                    round(range_start + end, 3),
                )
            )
    return output


def split_audio_plan(
    audio_path: Path,
    chunks_dir: Path,
    plan: list[tuple[int, float, float]],
    timeout: float,
    deadline: float | None = None,
) -> list[Path]:
    """按分片计划用 ffmpeg 切分 16k 单声道 mp3。"""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for index, start, end in plan:
        step_timeout = timeout
        if deadline is not None:
            step_timeout = min(step_timeout, deadline - time.perf_counter())
        if step_timeout <= 0:
            raise TimeoutError("audio splitting budget exhausted")
        output = chunks_dir / f"chunk_{index:03d}.mp3"
        run_ffmpeg(
            [
                "-ss",
                str(start),
                "-i",
                str(audio_path),
                "-t",
                str(max(0.1, end - start)),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "64k",
                str(output),
            ],
            step_timeout,
        )
        outputs.append(output)
    return outputs
