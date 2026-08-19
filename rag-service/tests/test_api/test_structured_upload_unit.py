from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from starlette.datastructures import Headers, UploadFile

from src.api.router_docs import _upload_doc_to_kb
from src.api.router_structured import _build_preview, _mapping_from_client, _profile_payload
from src.api.router_structured import router as structured_router
from src.shared.config import Settings
from src.structured.profiler import profile_source


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def scalar(self, _query):
        return None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = 41

    async def commit(self) -> None:
        return None

    async def refresh(self, _value: object) -> None:
        return None


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "app": {"browser_origin": "http://127.0.0.1:8000", "secure_cookie": False},
            "rag": {
                "chroma_mode": "persist", "chroma_persist_dir": str(tmp_path / "chroma"),
                "default_chunk_size": 512, "default_overlap": 50, "top_k": 5,
                "similarity_threshold": 0.2,
            },
            "model_relay": {
                "base_url": "http://127.0.0.1:9000/v1", "api_key": "test-key",
                "embedding_model": "test", "chat_model": "test", "timeout_seconds": 60,
                "embedding_max_retries": 3, "chat_pre_stream_max_retries": 1,
                "retry_base_delay_seconds": 0.01,
            },
            "database": {"url": "mysql+asyncmy://u:p@127.0.0.1/db", "pool_size": 2,
                         "pool_recycle_seconds": 1800},
            "upload": {"root_dir": str(tmp_path / "uploads"), "temp_dir": str(tmp_path / "tmp"),
                       "max_size_mb": 100, "allowed_extensions": ["txt"]},
            "llm": {"temperature": 0.7, "max_tokens": 4096, "max_history_tokens": 4096,
                    "system_prompt": "system"},
            "empty_response": "empty",
        }
    )


async def test_json_upload_bypasses_text_parser_and_waits_for_mapping(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    upload = UploadFile(
        filename="records.json",
        file=io.BytesIO(b'[{"title":"hello"}]'),
        headers=Headers({"content-type": "application/json"}),
    )
    db = _FakeSession()
    kb = SimpleNamespace(owner_user_id=7)

    doc, job_id = await _upload_doc_to_kb(request, db, 3, kb, upload)

    assert doc.status == "awaiting_mapping"
    assert job_id is None
    assert Path(settings.upload.root_dir, doc.file_path).read_bytes() == b'[{"title":"hello"}]'
    assert len(db.added) == 1


def test_profile_payload_matches_json_mapping_wizard_contract(tmp_path: Path) -> None:
    source = tmp_path / "records.json"
    source.write_text('[{"title":"hello","body":"' + ("content " * 20) + '"}]', encoding="utf-8")

    payload = _profile_payload(41, profile_source(source, "json"))

    assert payload["doc_id"] == 41
    assert payload["source_format"] == "json"
    assert payload["total_records_estimate"] == 1
    candidate = payload["candidates"][0]
    assert candidate["record_path"] == "$"
    assert candidate["fields"]
    mapping = candidate["suggested_mapping"]
    assert mapping["source_format"] == "json"
    assert mapping["record_types"][0]["fields"]


def test_preview_post_route_is_registered() -> None:
    assert any(
        route.path == "/api/kb/{kb_id}/json/preview" and "POST" in route.methods
        for route in structured_router.routes
    )


def test_confirm_flow_routes_are_registered() -> None:
    methods_by_path: dict[str, set[str]] = {}
    for route in structured_router.routes:
        methods_by_path.setdefault(route.path, set()).update(route.methods)
    assert "GET" in methods_by_path["/api/mapping-templates"]
    assert "POST" in methods_by_path["/api/mapping-templates"]
    assert "GET" in methods_by_path["/api/mapping-templates/{template_id}"]
    assert "PUT" in methods_by_path["/api/mapping-templates/{template_id}"]
    assert "DELETE" in methods_by_path["/api/mapping-templates/{template_id}"]
    assert "POST" in methods_by_path["/api/kb/{kb_id}/json/ingest"]


def test_preview_maps_parent_and_child_records_from_client_contract(tmp_path: Path) -> None:
    source = tmp_path / "topic.json"
    source.write_text(
        '{"id":"p1","body":"parent content","comments":['
        '{"author":"A","text":"first answer"},'
        '{"author":"B","referTo":"A","text":"follow up"}]}'
        ,
        encoding="utf-8",
    )
    mapping = _mapping_from_client(
        {
            "source_format": "json",
            "record_types": [
                {
                    "name": "record",
                    "record_path": "$",
                    "chunk_policy": "semantic",
                    "relation_rule": None,
                    "fields": [
                        {"path": "id", "role": "id", "name": "id", "transforms": [], "required": True},
                        {"path": "body", "role": "content", "name": "body", "transforms": [], "required": True},
                    ],
                    "children": [
                        {
                            "name": "comment",
                            "record_path": "comments[*]",
                            "chunk_policy": "semantic",
                            "relation_rule": {
                                "source": "referTo", "target": "author",
                                "strategy": "nearest_previous_sibling",
                            },
                            "fields": [
                                {"path": "text", "role": "content", "name": "text", "transforms": [], "required": True}
                            ],
                            "children": [],
                        }
                    ],
                }
            ],
        }
    )

    preview = _build_preview(source, "json", mapping, "source-hash", 5)

    assert preview["total_rows"] == 3
    assert [row["record_type"] for row in preview["rows"]] == ["record", "comment", "comment"]
    assert preview["rows"][0]["embedding_text"] == "parent content"
    assert preview["rows"][1]["parent_id"] == "p1"
    assert preview["rows"][2]["parent_id"] == preview["rows"][1]["record_id"]

    limited = _build_preview(source, "json", mapping, "source-hash", 2)
    assert len(limited["rows"]) == 2
    assert limited["total_rows"] == 3
