"""处理产物：records.jsonl / mapping-errors.jsonl / manifest.json。

- 写入 `<upload_root>/processed/<doc_id>/<ingest_run_id>/`。
- records/errors 按行写入 UTF-8 JSONL，逐行 flush（进程中断最多丢最后一行）。
- finalize 关闭文件、计算哈希，经临时文件原子写入 manifest.json，
  并返回相对路径供 IngestRun 记录。
- 未 finalize 的运行只留下 run 目录，不产生 manifest，不会替换活跃产物。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from src.structured.models import MappedRecord, MappingIssue


class ArtifactWriter:
    """一次入库运行的处理产物写入器。非线程安全；单进程内使用。"""

    def __init__(
        self,
        upload_root: Path,
        doc_id: int,
        ingest_run_id: str,
        mapping_version_id: int,
    ) -> None:
        self._upload_root = Path(upload_root)
        self._run_dir = self._upload_root / "processed" / str(doc_id) / ingest_run_id
        self._run_dir.mkdir(parents=True, exist_ok=False)

        self._mapping_version_id = mapping_version_id
        self._records_path = self._run_dir / "records.jsonl"
        self._errors_path = self._run_dir / "mapping-errors.jsonl"
        self._manifest_path = self._run_dir / "manifest.json"

        self._records_file = self._records_path.open("w", encoding="utf-8", newline="\n")
        self._errors_file = self._errors_path.open("w", encoding="utf-8", newline="\n")
        self._finalized = False

    # ---------------------------------------------------------------- 写入

    def write_record(self, record: MappedRecord) -> None:
        self._check_open()
        payload = json.dumps(
            record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )
        self._records_file.write(payload + "\n")
        self._records_file.flush()

    def write_error(self, issue: MappingIssue) -> None:
        self._check_open()
        payload = json.dumps(
            issue.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )
        self._errors_file.write(payload + "\n")
        self._errors_file.flush()

    # ---------------------------------------------------------------- 收尾

    def finalize(self) -> dict[str, Any]:
        """关闭文件、计算哈希、原子写入 manifest；返回 manifest 内容。"""
        if self._finalized:
            return self._manifest_data
        self._records_file.close()
        self._errors_file.close()

        records_hash = _sha256_file(self._records_path)
        errors_hash = _sha256_file(self._errors_path)
        total_records = _count_lines(self._records_path)
        error_count = _count_lines(self._errors_path)

        self._manifest_data = {
            "total_records": total_records,
            "error_count": error_count,
            "mapping_version_id": self._mapping_version_id,
            "records_hash": records_hash,
            "errors_hash": errors_hash,
            "records_path": _relative(self._records_path, self._upload_root),
            "errors_path": _relative(self._errors_path, self._upload_root),
            "manifest_path": _relative(self._manifest_path, self._upload_root),
        }
        _atomic_write_json(self._manifest_path, self._manifest_data)
        self._finalized = True
        return self._manifest_data

    def _check_open(self) -> None:
        if self._finalized:
            raise RuntimeError("ArtifactWriter 已 finalize，不能继续写入")

    # ---------------------------------------------------------------- 属性

    @property
    def records_path(self) -> Path:
        return self._records_path

    @property
    def errors_path(self) -> Path:
        return self._errors_path

    @property
    def manifest_path(self) -> Path:
        return self._manifest_path


def _relative(path: Path, upload_root: Path) -> str:
    """相对 upload_root 的路径（POSIX 分隔符），供 IngestRun 列存储。"""
    return path.relative_to(upload_root).as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for _ in handle:
            count += 1
    return count


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """经临时文件原子写入 JSON（写一半崩溃不会留下损坏的 manifest）。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)
