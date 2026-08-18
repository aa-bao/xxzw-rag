"""转录合并与时间戳工具：字幕/ASR 分片结果合并为带 [MM:SS] 时间戳的完整转写稿。

移植自 quick-watch 的合并逻辑（去除 CLI/锁/进度通知等外围），并适配
火山引擎录音文件极速识别（utterances 自带 begin/end 毫秒时间戳）的输出。
"""
from __future__ import annotations

import math
import re

# ── 时间工具 ──

def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def ts_to_seconds(ts: str) -> float:
    """解析 MM:SS、HH:MM:SS 或纯十进制秒（如 \"22.54\"）为浮点数。"""
    if ":" in ts:
        parts = [int(p) for p in ts.split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return float(parts[0])
    return float(ts)


# ── token 估算 ──

def estimate_text_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    non_cjk = re.sub(r"[\u3400-\u9fff\uf900-\ufaff\s]", "", text)
    english_words = len(re.findall(r"[A-Za-z0-9]+", text))
    return max(1, cjk + max(english_words, math.ceil(len(non_cjk) / 4)))


# ── 句子级时间戳估算（字幕+ASR 合并兜底 / 无内嵌时间戳时） ──

_SENTENCE_SPLIT_RE = re.compile(
    r"([\u3002\uff01\uff1f\uff1b\n!?;]+|[.]+(?=\s|$))"  # 中日韩 + 英文句子边界
)


def split_sentence_text(text: str) -> list[str]:
    """按中日韩/英文标点把文本切分成子句。"""
    parts = _SENTENCE_SPLIT_RE.split(text)
    result: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        buf += part
        if _SENTENCE_SPLIT_RE.fullmatch(part) and len(buf.strip()) > 1:
            result.append(buf.strip())
            buf = ""
    if buf.strip():
        result.append(buf.strip())
    if not result:
        result = [text.strip()]
    return result


def _estimate_sentence_timestamps(
    text: str, chunk_start: float, chunk_duration: float,
) -> list[tuple[float, str]]:
    """把分片文本切分成句子，并为每个句子分配按比例估算的时间戳。"""
    sentences = split_sentence_text(text)
    if not sentences:
        return []
    total_chars = sum(len(s) for s in sentences)
    if total_chars <= 0:
        total_chars = len(sentences)
    results: list[tuple[float, str]] = []
    elapsed = 0.0
    for s in sentences:
        char_weight = len(s) / max(total_chars, 1)
        uniform_weight = 1.0 / len(sentences)
        weight = char_weight * 0.6 + uniform_weight * 0.4
        sec = chunk_start + elapsed + weight * chunk_duration * 0.5
        elapsed += weight * chunk_duration
        results.append((round(sec, 1), s))
    return results


_TIMESTAMP_LINE = re.compile(
    r"^\[(\d+(?:\.\d+)?|\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\]\s*(.*)"
)


def _parse_timestamped_lines(text: str) -> tuple[list[tuple[float, str]], int]:
    """解析文本中已带 [MM:SS] 时间戳的行。返回 (entries, count)。"""
    entries: list[tuple[float, str]] = []
    count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _TIMESTAMP_LINE.match(line)
        if m:
            sec = ts_to_seconds(m.group(1))
            body = m.group(2).strip()
            if body:
                entries.append((sec, body))
                count += 1
    return entries, count


# ── 分片结果合并 ──

def merge_chunk_results(
    results: list[dict[str, object]],
    chunk_durations: dict[int, float] | None = None,
) -> dict[str, object]:
    """把 ASR 各分片结果合并成一份完整转写稿。

    每个分片文本可携带 [MM:SS.sss] 时间戳行（优先）；无内嵌时间戳时回退
    按比例估算。返回 {transcript, partial, failed_chunks}。
    """
    ordered = sorted(results, key=lambda item: int(item["index"]))
    failed = [int(item["index"]) for item in ordered if item.get("error")]
    all_entries: list[tuple[float, str]] = []

    for item in ordered:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        entries, ts_count = _parse_timestamped_lines(text)
        if ts_count:
            all_entries.extend(entries)
            continue  # 已带时间戳——跳过按比例估算

        # 兜底：无结构的纯文本 → 按比例估算。
        start_sec = float(item["start"])
        chunk_len = (chunk_durations or {}).get(int(item["index"]))
        if chunk_len is None or chunk_len <= 0:
            chunk_len = 150.0
        sentences = _estimate_sentence_timestamps(text, start_sec, chunk_len)
        if sentences:
            all_entries.extend(sentences)
        else:
            all_entries.append((start_sec, text))

    lines = [
        f"[{format_time(sec)}] {txt}"
        for sec, txt in sorted(all_entries, key=lambda x: x[0])
    ]
    return {
        "transcript": "\n\n".join(lines),
        "partial": bool(failed),
        "failed_chunks": failed,
    }


def merge_resume_results(
    saved: list[dict[str, object]], new: list[dict[str, object]]
) -> list[dict[str, object]]:
    by_index = {int(item["index"]): item for item in saved}
    for item in new:
        index = int(item["index"])
        previous = by_index.get(index)
        if previous is None or previous.get("error") is not None or item.get("error") is None:
            combined = dict(item)
            if previous:
                combined["billable_audio_seconds"] = round(
                    float(previous.get("billable_audio_seconds") or 0)
                    + float(item.get("billable_audio_seconds") or 0),
                    3,
                )
                combined["total_attempts"] = int(
                    previous.get("total_attempts") or previous.get("attempts") or 0
                ) + int(item.get("total_attempts") or item.get("attempts") or 0)
            by_index[index] = combined
    return [by_index[index] for index in sorted(by_index)]


def merge_caption_and_asr(
    cues: list[dict[str, object]], results: list[dict[str, object]],
    chunk_durations: dict[int, float] | None = None,
) -> str:
    """把字幕与 ASR 转写稿合并成一份带时间戳的完整转写稿。"""
    entries: list[tuple[float, str]] = [
        (float(cue["start"]), str(cue.get("text") or "").strip()) for cue in cues
    ]
    for result in results:
        text = str(result.get("text") or "").strip()
        if not text:
            continue
        parsed, ts_count = _parse_timestamped_lines(text)
        if ts_count:
            entries.extend(parsed)
            continue
        start_sec = float(result["start"])
        chunk_len = (chunk_durations or {}).get(int(result["index"]))
        if chunk_len is None or chunk_len <= 0:
            chunk_len = 150.0
        sentences = _estimate_sentence_timestamps(text, start_sec, chunk_len)
        if sentences:
            entries.extend(sentences)
        else:
            entries.append((start_sec, text))
    return "\n\n".join(
        f"[{format_time(start)}] {text}" for start, text in sorted(entries) if text
    )


def format_utterance_entries(
    utterances: list[dict[str, object]],
    chunk_start: float = 0.0,
) -> list[tuple[float, str]]:
    """把火山 ASR 返回的 utterances（毫秒时间戳）转成 (秒, 文本) 条目。

    句子过长时按标点切分，时间戳按字符权重在句内线性插值。
    """
    entries: list[tuple[float, str]] = []
    for u in utterances:
        text = str(u.get("text") or "").strip()
        if not text:
            continue
        start_ms = float(u.get("start_time") or 0)
        end_ms = float(u.get("end_time") or start_ms)
        absolute = round(chunk_start + start_ms / 1000.0, 3)
        subs = split_sentence_text(text)
        if len(subs) <= 1:
            entries.append((absolute, text))
        else:
            dur_s = max(end_ms - start_ms, 1) / 1000.0
            delta = start_ms / 1000.0
            for sub in subs:
                weight = len(sub) / max(len(text), 1)
                sub_delta = delta + weight * dur_s
                entries.append((round(chunk_start + sub_delta, 3), sub))
    return entries
