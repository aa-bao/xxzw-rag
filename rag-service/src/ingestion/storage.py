from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol


_UPLOAD_CHUNK_SIZE = 1024 * 1024


class AsyncUpload(Protocol):
    async def read(self, size: int) -> bytes: ...


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


async def atomic_save_upload(
    root_dir: Path,
    temp_dir: Path,
    upload: AsyncUpload,
    filename: str,
    *,
    max_size_bytes: int,
) -> tuple[str, int, str]:
    """Persist an async upload without buffering the complete file in memory."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    extension = os.path.splitext(filename)[1] or ".txt"
    generated = f"{uuid.uuid4().hex}{extension}"
    temp_path = (temp_dir / generated).resolve()
    digest = hashlib.sha256()
    size = 0

    try:
        with temp_path.open("wb") as target:
            while chunk := await upload.read(_UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > max_size_bytes:
                    from src.shared.errors import AppError

                    raise AppError("FILE_TOO_LARGE", f"文件大小超出限制 ({max_size_bytes // (1024*1024)} MB)")
                digest.update(chunk)
                target.write(chunk)

        relative_path = f"{generated[:2]}/{generated[2:4]}/{generated}"
        final_path = root_dir / relative_path
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return final_path.relative_to(root_dir).as_posix(), size, digest.hexdigest()
