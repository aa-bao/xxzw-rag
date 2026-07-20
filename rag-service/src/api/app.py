from __future__ import annotations

from fastapi import FastAPI

from src.shared.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Local RAG Knowledge Base")
    app.state.settings = settings

    @app.get("/api/health/live")
    async def live() -> dict[str, object]:
        return {"success": True, "data": {"status": "live"}}

    return app

