from importlib import import_module

import pytest
from httpx import ASGITransport, AsyncClient


def _load_create_app():
    try:
        module = import_module("src.api.app")
    except ModuleNotFoundError:
        return None
    return getattr(module, "create_app", None)


async def test_live_does_not_require_dependencies(monkeypatch) -> None:
    create_app = _load_create_app()
    assert create_app is not None, "create_app has not been implemented"

    monkeypatch.setenv("MODEL_RELAY_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("MODEL_RELAY_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "t")
    monkeypatch.setenv("CHAT_MODEL", "t")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@127.0.0.1:3306/db")

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "live"}}
