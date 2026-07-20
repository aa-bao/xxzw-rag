from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from src.ingestion.storage import atomic_save


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
