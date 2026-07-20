from importlib import import_module

from httpx import ASGITransport, AsyncClient


def _load_create_app():
    try:
        module = import_module("src.api.app")
    except ModuleNotFoundError:
        return None
    return getattr(module, "create_app", None)


async def test_live_does_not_require_dependencies() -> None:
    create_app = _load_create_app()
    assert create_app is not None, "create_app has not been implemented"

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "live"}}
