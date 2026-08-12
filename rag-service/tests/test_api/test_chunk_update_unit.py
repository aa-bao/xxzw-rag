from pydantic import ValidationError
import pytest

from src.api.router_docs import UpdateChunkRequest, router


def test_chunk_update_route_is_registered() -> None:
    methods_by_path: dict[str, set[str]] = {}
    for route_item in router.routes:
        methods_by_path.setdefault(route_item.path, set()).update(route_item.methods or set())

    assert "PUT" in methods_by_path[
        "/api/kb/{kb_id}/docs/{doc_id}/chunks/{chunk_id}"
    ]


def test_chunk_update_content_has_embedding_safe_limit() -> None:
    assert UpdateChunkRequest(content="updated").content == "updated"
    with pytest.raises(ValidationError):
        UpdateChunkRequest(content="x" * 501)
