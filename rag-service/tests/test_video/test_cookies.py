"""抖音 / 元宝 Cookie 模块单元测试：解析、校验、保存、备份、状态。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.video.cookies import (
    get_cookie_status,
    parse_cookie_header,
    parse_netscape_cookie,
    save_douyin_cookie,
    save_yuanbao_cookie,
)

_DOUYIN_COOKIE = """# Netscape HTTP Cookie File
#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tsecret_value
.douyin.com\tTRUE\t/\tFALSE\t1893456000\tttwid\t1%3Dvalue
"""


def test_parse_netscape_cookie_accepts_http_only_and_domains() -> None:
    cookies = parse_netscape_cookie(_DOUYIN_COOKIE)
    assert len(cookies) == 2
    assert cookies[0]["name"] == "sessionid"
    assert cookies[0]["domain"] == ".douyin.com"
    assert cookies[0]["secure"] is True
    assert cookies[1]["domain"] == ".douyin.com"


def test_parse_netscape_cookie_rejects_missing_douyin_domain() -> None:
    content = (
        "# comment\n"
        ".example.com\tTRUE\t/\tFALSE\t1893456000\ta\tb\n"
    )
    with pytest.raises(ValueError, match="douyin.com|iesdouyin.com"):
        parse_netscape_cookie(content)


def test_parse_netscape_cookie_rejects_short_line() -> None:
    with pytest.raises(ValueError, match="7"):
        parse_netscape_cookie(".douyin.com\tTRUE\t/\tTRUE\t1893456000\tsessionid")


def test_parse_cookie_header() -> None:
    cookies = parse_cookie_header(" sessionid=abc123; uid=456 ; theme=dark ")
    assert cookies == [
        {"name": "sessionid", "value": "abc123"},
        {"name": "uid", "value": "456"},
        {"name": "theme", "value": "dark"},
    ]


def test_parse_cookie_header_rejects_missing_equals() -> None:
    with pytest.raises(ValueError, match="缺少 '='"):
        parse_cookie_header("sessionid=abc; bad_token")


def test_save_douyin_cookie_writes_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "cookies_douyin.txt"
    path.write_text("old-cookie-content", encoding="utf-8")

    result = save_douyin_cookie(_DOUYIN_COOKIE, path)

    assert result["configured"] is True
    assert result["cookie_count"] == 2
    assert result["domains"] == ["douyin.com"]
    assert path.read_text(encoding="utf-8") == _DOUYIN_COOKIE
    assert (tmp_path / "cookies_douyin.txt.bak").read_text(encoding="utf-8") == "old-cookie-content"
    assert "secret_value" not in json.dumps(result, ensure_ascii=False)


def test_save_yuanbao_cookie_writes_json_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    path.write_text("[]", encoding="utf-8")

    result = save_yuanbao_cookie("sessionid=abc123; uid=456", path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["name"] == "sessionid"
    assert data[0]["value"] == "abc123"
    assert data[0]["domain"] == ".tencent.com"
    assert data[1]["name"] == "uid"
    assert result["cookie_count"] == 2
    assert (tmp_path / "cookies.json.bak").read_text(encoding="utf-8") == "[]"
    assert "abc123" not in json.dumps(result, ensure_ascii=False)


def test_save_douyin_cookie_does_not_overwrite_on_invalid(tmp_path: Path) -> None:
    path = tmp_path / "cookies_douyin.txt"
    path.write_text("old", encoding="utf-8")

    with pytest.raises(ValueError):
        save_douyin_cookie("not a netscape cookie", path)

    assert path.read_text(encoding="utf-8") == "old"


def test_get_cookie_status_hides_plaintext(tmp_path: Path) -> None:
    douyin_path = tmp_path / "cookies_douyin.txt"
    yuanbao_path = tmp_path / "cookies.json"
    save_douyin_cookie(_DOUYIN_COOKIE, douyin_path)
    save_yuanbao_cookie("sessionid=abc123", yuanbao_path)

    status = get_cookie_status(douyin_path, yuanbao_path)
    text = json.dumps(status, ensure_ascii=False)

    assert status["douyin"]["configured"] is True
    assert status["douyin"]["cookie_count"] == 2
    assert status["douyin"]["domains"] == ["douyin.com"]
    assert status["yuanbao"]["configured"] is True
    assert status["yuanbao"]["cookie_count"] == 1
    assert "secret_value" not in text
    assert "abc123" not in text


def test_get_cookie_status_missing_files(tmp_path: Path) -> None:
    status = get_cookie_status(tmp_path / "missing.txt", tmp_path / "missing.json")

    assert status["douyin"]["configured"] is False
    assert status["douyin"]["cookie_count"] == 0
    assert status["douyin"]["error"] is None
    assert status["yuanbao"]["configured"] is False
    assert status["yuanbao"]["cookie_count"] == 0
