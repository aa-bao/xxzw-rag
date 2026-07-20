from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_save(
    root_dir: Path,
    temp_dir: Path,
    data: bytes,
    original_filename: str,
    *,
    max_size_bytes: int = 100 * 1024 * 1024,
) -> str:
    if len(data) > max_size_bytes:
        from src.shared.errors import AppError
        raise AppError("FILE_TOO_LARGE", f"文件大小超出限制 ({max_size_bytes // (1024*1024)} MB)")

    temp_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(original_filename)[1] or ".txt"
    generated = f"{uuid.uuid4().hex}{ext}"
    tmp_path = temp_dir / generated
    try:
        tmp_path.write_bytes(data)
        rel = f"{generated[:2]}/{generated[2:4]}/{generated}"
        target = root_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return rel
