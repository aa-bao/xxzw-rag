"""transcript 模块单元测试（纯逻辑，无需网络/凭证）。"""
from __future__ import annotations

from src.video.transcript import (
    estimate_text_tokens,
    format_time,
    format_utterance_entries,
    merge_caption_and_asr,
    merge_chunk_results,
    split_sentence_text,
    ts_to_seconds,
)


def test_format_time() -> None:
    assert format_time(0) == "00:00"
    assert format_time(65) == "01:05"
    assert format_time(3661) == "01:01:01"
    assert format_time(-5) == "00:00"


def test_ts_to_seconds() -> None:
    assert ts_to_seconds("01:05") == 65
    assert ts_to_seconds("01:01:01") == 3661
    assert ts_to_seconds("22.54") == 22.54


def test_split_sentence_text() -> None:
    parts = split_sentence_text("今天天气不错。我们出门散步吧！然后回家吃饭")
    assert parts == ["今天天气不错。", "我们出门散步吧！", "然后回家吃饭"]
    # 英文句点
    assert split_sentence_text("Hello world. Next sentence.") == [
        "Hello world.",
        "Next sentence.",
    ]


def test_merge_chunk_results_with_timestamps() -> None:
    results = [
        {"index": 0, "start": 0.0, "text": "[00:00] 第一句\n[00:02] 第二句", "error": None},
        {"index": 1, "start": 150.0, "text": "[02:31] 第三句", "error": None},
    ]
    merged = merge_chunk_results(results)
    assert merged["partial"] is False
    assert merged["failed_chunks"] == []
    lines = merged["transcript"].split("\n\n")
    assert lines[0] == "[00:00] 第一句"
    assert lines[1] == "[00:02] 第二句"
    assert lines[2] == "[02:31] 第三句"


def test_merge_chunk_results_proportional_fallback() -> None:
    results = [{"index": 0, "start": 0.0, "text": "没有时间戳的纯文本。", "error": None}]
    merged = merge_chunk_results(results, chunk_durations={0: 10.0})
    assert merged["transcript"].startswith("[00:0")
    assert "纯文本" in merged["transcript"]


def test_merge_chunk_results_partial_failure() -> None:
    results = [
        {"index": 0, "start": 0.0, "text": "[00:00] 好的", "error": None},
        {"index": 1, "start": 10.0, "text": "", "error": "timeout"},
    ]
    merged = merge_chunk_results(results)
    assert merged["partial"] is True
    assert merged["failed_chunks"] == [1]


def test_merge_caption_and_asr() -> None:
    cues = [{"start": 0.0, "end": 2.0, "text": "字幕一"}]
    results = [{"index": 0, "start": 5.0, "text": "[00:05] ASR 句子", "error": None}]
    merged = merge_caption_and_asr(cues, results)
    assert "[00:00] 字幕一" in merged
    assert "[00:05] ASR 句子" in merged


def test_format_utterance_entries() -> None:
    utterances = [
        {"start_time": 0, "end_time": 1000, "text": "第一句。"},
        {"start_time": 1000, "end_time": 2500, "text": "第二句。"},
    ]
    entries = format_utterance_entries(utterances, chunk_start=150.0)
    assert entries[0][0] == 150.0
    assert entries[1][0] == 151.0
    assert entries[0][1] == "第一句。"


def test_estimate_text_tokens() -> None:
    assert estimate_text_tokens("你好世界") >= 4
    assert estimate_text_tokens("hello world") >= 2
    assert estimate_text_tokens("") == 1
