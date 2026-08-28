"""抖音 / 元宝 Cookie 解析、校验与原子保存。

- 抖音：Netscape HTTP Cookie 文件格式（支持 #HttpOnly_ 前缀）。
- 元宝：Cookie Header 格式（name=value; name2=value2），保存为 wx-channels 可读的 JSON。
- 状态接口只返回元信息，不返回 Cookie 明文。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
_YUANBAO_DOMAIN = ".tencent.com"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """同一目录临时文件 + os.replace，保证写文件原子性。"""
    _ensure_parent(path)
    fd, tmp = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: Path) -> None:
    """覆盖前把现有文件备份为 <文件名>.bak；备份失败不阻断新写入。"""
    if not path.is_file():
        return
    backup = path.with_name(path.name + ".bak")
    try:
        shutil.copy2(path, backup)
    except OSError:
        pass


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")
    except OSError:
        return None


def parse_netscape_cookie(text: str) -> list[dict[str, Any]]:
    """解析并校验 Netscape Cookie 文本。

    校验规则：
    - 普通注释行（# 开头且非 #HttpOnly_）和空行忽略；
    - 非注释行必须至少有 7 个 tab 字段；
    - 整个文件至少包含一条 douyin.com / iesdouyin.com 域名的 Cookie。
    """
    cookies: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue

        fields = line.split("\t")
        if len(fields) < 7:
            raise ValueError("Netscape Cookie 行必须至少包含 7 个 tab 分隔字段")

        raw_domain = fields[0]
        domain = raw_domain[len("#HttpOnly_"):] if raw_domain.startswith("#HttpOnly_") else raw_domain
        domain_lower = domain.lower().lstrip(".")
        if not any(d in domain_lower for d in _DOUYIN_DOMAINS):
            # 允许文件里混有其他站点 Cookie，但不会计入抖音域名。
            continue

        try:
            expires = int(fields[4])
        except ValueError:
            expires = 0

        cookies.append({
            "domain": domain,
            "include_subdomains": fields[1].lower() == "true",
            "path": fields[2],
            "secure": fields[3].lower() == "true",
            "expires": expires,
            "name": fields[5],
            "value": fields[6],
        })

    if not cookies:
        raise ValueError("Netscape Cookie 必须至少包含一条 douyin.com 或 iesdouyin.com 的 Cookie")
    return cookies


def parse_cookie_header(text: str) -> list[dict[str, str]]:
    """解析并校验元宝 Cookie Header：name=value; name2=value2。"""
    if not text or not text.strip():
        raise ValueError("Cookie Header 不能为空")

    cookies: list[dict[str, str]] = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Cookie Header 段缺少 '='：{part}")
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            raise ValueError("Cookie Header 存在空名称")
        if not value:
            raise ValueError(f"Cookie Header 的 {name} 缺少值")
        cookies.append({"name": name, "value": value})

    if not cookies:
        raise ValueError("Cookie Header 未解析到任何 Cookie")
    return cookies


def save_douyin_cookie(content: str, path: Path) -> dict[str, Any]:
    """校验 Netscape 格式后原子写入抖音 Cookie 文件，并备份旧文件。"""
    parsed = parse_netscape_cookie(content)
    _backup(path)
    _atomic_write(path, content)
    return {
        "configured": True,
        "path": str(path),
        "cookie_count": len(parsed),
        "domains": sorted({c["domain"].lstrip(".") or c["domain"] for c in parsed}),
        "updated_at": _mtime_iso(path),
        "error": None,
    }


def save_yuanbao_cookie(content: str, path: Path) -> dict[str, Any]:
    """校验 Cookie Header 后原子写入 wx-channels 使用的 JSON 文件，并备份旧文件。"""
    parsed = parse_cookie_header(content)
    payload = [
        {
            "name": item["name"],
            "value": item["value"],
            "domain": _YUANBAO_DOMAIN,
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
            "expires": -1,
        }
        for item in parsed
    ]
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _backup(path)
    _atomic_write(path, body)
    return {
        "configured": True,
        "path": str(path),
        "cookie_count": len(parsed),
        "domains": [_YUANBAO_DOMAIN],
        "updated_at": _mtime_iso(path),
        "error": None,
    }


def _douyin_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "configured": False,
            "path": str(path),
            "cookie_count": 0,
            "domains": [],
            "updated_at": None,
            "error": None,
        }
    try:
        parsed = parse_netscape_cookie(path.read_text(encoding="utf-8", errors="replace"))
        return {
            "configured": True,
            "path": str(path),
            "cookie_count": len(parsed),
            "domains": sorted({c["domain"].lstrip(".") or c["domain"] for c in parsed}),
            "updated_at": _mtime_iso(path),
            "error": None,
        }
    except (ValueError, OSError) as exc:
        return {
            "configured": False,
            "path": str(path),
            "cookie_count": 0,
            "domains": [],
            "updated_at": _mtime_iso(path),
            "error": str(exc),
        }


def _yuanbao_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "configured": False,
            "path": str(path),
            "cookie_count": 0,
            "domains": [],
            "updated_at": None,
            "error": None,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("元宝 Cookie 文件必须是 JSON 数组")
        cookies = [
            item for item in data
            if isinstance(item, dict) and item.get("name") and "value" in item
        ]
        if not cookies:
            raise ValueError("元宝 Cookie 文件未包含有效 Cookie")
        domains = sorted({
            str(item.get("domain") or _YUANBAO_DOMAIN)
            for item in data
            if isinstance(item, dict) and item.get("domain")
        }) or [_YUANBAO_DOMAIN]
        return {
            "configured": True,
            "path": str(path),
            "cookie_count": len(cookies),
            "domains": domains,
            "updated_at": _mtime_iso(path),
            "error": None,
        }
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        return {
            "configured": False,
            "path": str(path),
            "cookie_count": 0,
            "domains": [],
            "updated_at": _mtime_iso(path),
            "error": str(exc),
        }


def get_cookie_status(douyin_path: Path, yuanbao_path: Path) -> dict[str, Any]:
    """返回状态元信息，绝不包含 Cookie 明文。"""
    return {
        "douyin": _douyin_status(douyin_path),
        "yuanbao": _yuanbao_status(yuanbao_path),
    }
