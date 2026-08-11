from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from src.ingestion.storage import atomic_save, atomic_save_upload


class FakeAsyncUpload:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_atomic_save_uses_generated_name_not_original() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "uploads"
        root.mkdir()
        stored = atomic_save(root, Path(tmp) / "tmp", b"hello world", "secrets.txt")

        assert "secrets.txt" not in str(stored)
        assert (root / stored).read_bytes() == b"hello world"


def test_atomic_save_rejects_large_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "uploads"
        root.mkdir()

        from src.shared.errors import AppError

        with pytest.raises(AppError) as error:
            # 1 MB > default limit in test
            atomic_save(root, Path(tmp) / "tmp", b"x" * (1_048_577), "big.txt", max_size_bytes=1_048_576)

        assert error.value.code == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_atomic_save_upload_streams_and_returns_metadata(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    temp = tmp_path / "tmp"
    root.mkdir()
    payload = b"x" * (2 * 1024 * 1024)
    upload = FakeAsyncUpload(payload)

    result = await atomic_save_upload(
        root,
        temp,
        upload,
        "records.json",
        max_size_bytes=3 * 1024 * 1024,
    )

    assert (root / result[0]).read_bytes() == payload
    assert result[1] == len(payload)
    assert result[2] == hashlib.sha256(payload).hexdigest()
    assert len(upload.read_sizes) > 1
    assert max(upload.read_sizes) <= 1024 * 1024


@pytest.mark.asyncio
async def test_atomic_save_upload_rejects_large_file_and_removes_temp(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    temp = tmp_path / "tmp"
    root.mkdir()
    upload = FakeAsyncUpload(b"x" * (1024 * 1024 + 1))

    from src.shared.errors import AppError

    with pytest.raises(AppError) as error:
        await atomic_save_upload(
            root,
            temp,
            upload,
            "records.json",
            max_size_bytes=1024 * 1024,
        )

    assert error.value.code == "FILE_TOO_LARGE"
    assert not list(temp.iterdir())
    assert not list(root.rglob("*"))
