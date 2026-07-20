from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.router_auth import router as auth_router
from src.api.router_kb import router as kb_router
from src.shared.config import Settings
from src.shared.errors import AppError


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    app = FastAPI(title="Local RAG Knowledge Base")
    app.state.settings = settings
    if session_factory is not None:
        app.state.session_factory = session_factory

    app.include_router(auth_router)
    app.include_router(kb_router)

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/api/health/live")
    async def live() -> dict[str, object]:
        return {"success": True, "data": {"status": "live"}}

    return app

