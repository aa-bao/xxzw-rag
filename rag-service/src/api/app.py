from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.router_auth import router as auth_router
from src.api.router_chat import router as chat_router
from src.api.router_docs import router as docs_router
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
    app.include_router(docs_router)
    app.include_router(chat_router)

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/api/health/live")
    async def live() -> dict[str, object]:
        return {"success": True, "data": {"status": "live"}}

    # SPA static fallback: serve built assets, redirect unknown GET routes to index.html
    static_dir = Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist"
    if not static_dir.exists():
        static_dir = Path(__file__).resolve().parent.parent.parent / "static"

    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")

    return app

