"""处理产物测试：records.jsonl / mapping-errors.jsonl / manifest.json 原子性。"""
from __future__ import annotations

import json
from pathlib import Path

from src.structured.artifacts import ArtifactWriter
from src.structured.models import MappedRecord, MappingIssue


def _record(i: int, record_type: str = "post") -> MappedRecord:
    return MappedRecord(
        record_id=f"id-{i}",
        parent_id=None,
        record_type=record_type,
        title=f"标题 {i}",
        content=f"正文内容 {i}",
        keywords=("tag",),
        filters={"category": "x"},
        timestamps={},
        display={},
        raw={"id": i, "title": f"标题 {i}"},
        source_pointer=f"/posts/{i}",
        mapping_version_id=1,
    )


def _issue(i: int) -> MappingIssue:
    return MappingIssue(
        kind="warning",
        record_type="comment",
        source_pointer=f"/posts/0/comments/{i}",
        code="FIELD_EMPTY",
        message="字段为空",
        field="content",
    )


def test_write_record_and_error_produce_utf8_jsonl(tmp_path: Path) -> None:
    writer = ArtifactWriter(
        upload_root=tmp_path, doc_id=1, ingest_run_id="run-abc", mapping_version_id=1
    )
    writer.write_record(_record(1))
    writer.write_record(_record(2))
    writer.write_error(_issue(0))
    manifest = writer.finalize()

    records_path = tmp_path / "processed" / "1" / "run-abc" / "records.jsonl"
    assert records_path.exists()
    lines = records_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["record_id"] == "id-1"
    assert parsed[0]["record_type"] == "post"
    assert parsed[0]["mapping_version_id"] == 1
    assert parsed[0]["raw"] == {"id": 1, "title": "标题 1"}
    assert parsed[0]["title"] == "标题 1"

    errors_path = tmp_path / "processed" / "1" / "run-abc" / "mapping-errors.jsonl"
    error_lines = errors_path.read_text(encoding="utf-8").splitlines()
    assert len(error_lines) == 1
    error = json.loads(error_lines[0])
    assert error["kind"] == "warning"
    assert error["code"] == "FIELD_EMPTY"

    # manifest：计数、哈希与映射版本
    assert manifest is not None
    assert manifest["total_records"] == 2
    assert manifest["error_count"] == 1
    assert manifest["mapping_version_id"] == 1
    assert manifest["records_hash"]
    assert manifest["errors_hash"]
    assert manifest["records_path"].endswith("processed/1/run-abc/records.jsonl")
    assert manifest["errors_path"].endswith("mapping-errors.jsonl")

    manifest_path = tmp_path / "processed" / "1" / "run-abc" / "manifest.json"
    assert manifest_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_interrupted_run_leaves_only_run_dir(tmp_path: Path) -> None:
    # 前一次成功的运行留下产物
    good = ArtifactWriter(
        upload_root=tmp_path, doc_id=1, ingest_run_id="run-old", mapping_version_id=1
    )
    good.write_record(_record(1))
    good.finalize()
    good_records = tmp_path / "processed" / "1" / "run-old" / "records.jsonl"
    assert good_records.exists()

    # 新运行中途失败：不 finalize → 只留下 run 目录，不替换活跃产物
    failed = ArtifactWriter(
        upload_root=tmp_path, doc_id=1, ingest_run_id="run-new", mapping_version_id=1
    )
    failed.write_record(_record(2))
    failed.write_error(_issue(0))

    run_new = tmp_path / "processed" / "1" / "run-new"
    assert run_new.exists()
    assert (run_new / "records.jsonl").exists()
    # 活跃路径没有被替换：manifest 只在 finalize 后写入
    assert not (run_new / "manifest.json").exists()
    # 旧运行产物不受影响
    assert good_records.exists()


def test_finalize_returns_relative_paths(tmp_path: Path) -> None:
    writer = ArtifactWriter(
        upload_root=tmp_path, doc_id=42, ingest_run_id="run-1", mapping_version_id=2
    )
    writer.write_record(_record(1))
    manifest = writer.finalize()

    assert not Path(manifest["records_path"]).is_absolute()
    assert not Path(manifest["manifest_path"]).is_absolute()
    assert (tmp_path / manifest["records_path"]).exists()


def test_content_hash_matches_file(tmp_path: Path) -> None:
    import hashlib

    writer = ArtifactWriter(
        upload_root=tmp_path, doc_id=1, ingest_run_id="run-1", mapping_version_id=1
    )
    writer.write_record(_record(1))
    manifest = writer.finalize()

    expected = hashlib.sha256(
        (tmp_path / manifest["records_path"]).read_bytes()
    ).hexdigest()
    assert manifest["records_hash"] == expected
