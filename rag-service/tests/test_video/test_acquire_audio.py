"""audio / acquire 模块单元测试（纯逻辑；不触碰 ffmpeg/网络）。"""
from __future__ import annotations

import pytest

from src.video.acquire import (
    _parse_weixin_resolved,
    caption_coverage,
    caption_needs_asr,
    choose_asr_ranges,
    douyin_video_id,
    is_douyin_url,
    is_url,
    is_weixin_sph_url,
    normalize_douyin_url,
    parse_vtt_cues,
    parse_vtt_text,
    select_asr_ranges,
    validate_public_url,
    weixin_sph_id,
    weixin_sph_share_url,
)
from src.video.audio import parse_silence_points, plan_asr_chunks, plan_vad_chunks


# ── acquire: URL 校验 ──

def test_is_url() -> None:
    assert is_url("https://www.bilibili.com/video/BV1xx411c7mD") is True
    assert is_url("C:/videos/a.mp4") is False
    assert is_url("") is False


def test_validate_public_url_rejects_private() -> None:
    with pytest.raises(ValueError):
        validate_public_url("http://localhost:3000/video")
    with pytest.raises(ValueError):
        validate_public_url("http://127.0.0.1/x.mp4")
    with pytest.raises(ValueError):
        validate_public_url("ftp://example.com/x")


def test_is_weixin_sph_url() -> None:
    assert is_weixin_sph_url("https://weixin.qq.com/sph/AbCd123") is True
    assert is_weixin_sph_url(
        "https://channels.weixin.qq.com/finder-preview/pages/sph?id=A1UG8y1n3l"
    ) is True
    assert is_weixin_sph_url(
        "https://channels.weixin.qq.com/web/pages/feed?oid=abc&nid=def"
    ) is True
    assert is_weixin_sph_url("https://www.bilibili.com/video/BV1xx") is False


def test_weixin_sph_id() -> None:
    assert weixin_sph_id("https://weixin.qq.com/sph/AbCd123") == "AbCd123"
    assert (
        weixin_sph_id(
            "https://channels.weixin.qq.com/finder-preview/pages/sph?id=A1UG8y1n3l"
        )
        == "A1UG8y1n3l"
    )
    assert (
        weixin_sph_id(
            "https://channels.weixin.qq.com/web/pages/feed?oid=abc&nid=def"
        )
        == "abc"
    )
    assert (
        weixin_sph_share_url(
            "https://channels.weixin.qq.com/finder-preview/pages/sph?id=A1UG8y1n3l"
        )
        == "https://weixin.qq.com/sph/A1UG8y1n3l"
    )
    assert weixin_sph_share_url("https://weixin.qq.com/sph/AbCd123") == "https://weixin.qq.com/sph/AbCd123"


def test_parse_weixin_resolved_supports_legacy_and_feed_profile() -> None:
    legacy = {
        "code": 0,
        "msg": "成功",
        "data": {
            "data": {
                "authorInfo": {"nickname": "作者"},
                "feedInfo": {
                    "description": "标题",
                    "videoUrl": "https://finder.video.qq.com/xxx",
                    "coverUrl": "https://finder.video.qq.com/cover",
                },
            },
            "errCode": 0,
            "errMsg": "",
        },
    }
    assert _parse_weixin_resolved(legacy)["url"] == "https://finder.video.qq.com/xxx"

    feed_profile = {
        "code": 0,
        "msg": "成功",
        "data": {
            "errCode": 0,
            "errMsg": "ok",
            "data": {
                "object": {
                    "contact": {"nickname": "作者"},
                    "objectDesc": {
                        "description": "标题",
                        "media": [{
                            "url": "https://finder.video.qq.com/xxx",
                            "urlToken": "&token=abc",
                            "coverUrl": "https://finder.video.qq.com/cover",
                        }],
                    },
                }
            },
        },
    }
    assert (
        _parse_weixin_resolved(feed_profile)["url"]
        == "https://finder.video.qq.com/xxx&token=abc"
    )


# ── acquire: 抖音链接归一 ──

def test_is_douyin_url() -> None:
    assert is_douyin_url("https://www.douyin.com/jingxuan?modal_id=123456") is True
    assert is_douyin_url("https://www.iesdouyin.com/share/video/123456/") is True
    assert is_douyin_url("https://v.douyin.com/abc123/") is True
    assert is_douyin_url("https://www.bilibili.com/video/BV1xx") is False


def test_douyin_video_id() -> None:
    assert douyin_video_id("https://www.douyin.com/jingxuan?modal_id=7673818673068477722") == "7673818673068477722"
    assert douyin_video_id("https://www.douyin.com/video/7673818673068477722") == "7673818673068477722"
    assert douyin_video_id("https://www.iesdouyin.com/share/video/7673818673068477722/") == "7673818673068477722"
    assert douyin_video_id("https://v.douyin.com/abc123/") is None
    assert douyin_video_id("https://www.bilibili.com/video/BV1xx") is None


def test_normalize_douyin_url() -> None:
    assert normalize_douyin_url(
        "https://www.douyin.com/jingxuan?modal_id=7673818673068477722"
    ) == "https://www.douyin.com/video/7673818673068477722"
    assert normalize_douyin_url(
        "https://www.douyin.com/video/7673818673068477722"
    ) == "https://www.douyin.com/video/7673818673068477722"
    # 短链没有直接可见 ID，原样返回
    assert normalize_douyin_url("https://v.douyin.com/abc123/") == "https://v.douyin.com/abc123/"


# ── acquire: VTT 字幕 ──

_VTT = """WEBVTT

00:00.000 --> 00:02.500
大家好

00:02.500 --> 00:05.000
欢迎观看

"""


def test_parse_vtt_text() -> None:
    text = parse_vtt_text(_VTT)
    assert "[00:00] 大家好" in text
    assert "[00:02] 欢迎观看" in text


def test_parse_vtt_cues() -> None:
    cues = parse_vtt_cues(_VTT)
    assert len(cues) == 2
    assert cues[0]["start"] == 0.0
    assert cues[0]["text"] == "大家好"
    assert cues[1]["start"] == 2.5


# ── acquire: 覆盖率 ──

def test_caption_coverage_full() -> None:
    cues = [{"start": 0.0, "end": 10.0, "text": "x"}]
    coverage = caption_coverage(cues, 10.0)
    assert coverage["ratio"] == 1.0
    assert coverage["gaps"] == []


def test_caption_coverage_with_gap() -> None:
    cues = [{"start": 0.0, "end": 5.0, "text": "x"}, {"start": 8.0, "end": 10.0, "text": "y"}]
    coverage = caption_coverage(cues, 10.0)
    assert coverage["ratio"] == 0.7
    assert coverage["gaps"] == [(5.0, 8.0)]


def test_caption_needs_asr_and_ranges() -> None:
    coverage = {"ratio": 0.5, "gaps": [(5.0, 8.0)]}
    assert caption_needs_asr(coverage, 0.85) is True
    assert select_asr_ranges([(5.0, 8.0)], minimum_seconds=8.0) == []
    assert choose_asr_ranges(coverage, 10.0, 0.85) == [(0.0, 10.0)]
    # 覆盖率达标且 gap 足够大 → 只转写 gap
    coverage2 = {"ratio": 0.95, "gaps": [(100.0, 120.0)]}
    assert choose_asr_ranges(coverage2, 120.0, 0.85) == [(100.0, 120.0)]


# ── audio: 静音解析 / 分片规划 ──

def test_parse_silence_points() -> None:
    stderr = "silence_start: 10.0\nsilence_end: 10.5\nsilence_start: 20.0\nsilence_end: 21.0"
    assert parse_silence_points(stderr) == [10.25, 20.5]


def test_plan_vad_chunks_basic() -> None:
    plan = plan_vad_chunks(600.0, [])
    # 无静音点时按 target 边界切
    assert len(plan) >= 3
    assert plan[0][0] == 0
    assert plan[0][1] == 0.0
    # 相邻分片重叠（非首片）
    assert plan[1][1] < plan[1][2]


def test_plan_vad_chunks_prefers_silence() -> None:
    # 150s 目标边界处放一个静音点 → 边界取该点（首片尾 + 1.5s 重叠）
    plan = plan_vad_chunks(300.0, [150.0])
    assert plan[0][2] == 151.5
    assert plan[1][1] == 148.5


def test_plan_asr_chunks_with_ranges() -> None:
    ranges = [(0.0, 100.0), (200.0, 300.0)]
    plan = plan_asr_chunks(300.0, [], ranges)
    assert len(plan) == 2
    assert plan[0][1] >= 0.0 and plan[0][2] <= 100.0
    assert plan[1][1] >= 200.0 and plan[1][2] <= 300.0
