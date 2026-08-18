from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.router_auth import router as auth_router
from src.api.router_chat import router as chat_router
from src.api.router_docs import router as docs_router
from src.api.router_kb import router as kb_router
from src.api.router_platform import router as platform_router
from src.api.router_settings import router as settings_router
from src.api.router_structured import router as structured_router
from src.api.router_users import router as users_router
from src.video.config import VideoConfig
from src.video.router import router as video_router
from src.video.service import VideoTaskManager
from src.db.models import ModelSetting
from src.ingestion.worker import IngestWorker
from src.models.client import ModelRelayClient
from src.retrieval.chroma import ChromaRetrieval
from src.shared.config import Settings
from src.shared.errors import AppError
from src.shared.runtime import RuntimeModelRelay
from src.platform.config import PlatformConfig, conformance_mode
from src.platform.client import ControlPlaneClient


def _make_settings_shell(runtime_relay: RuntimeModelRelay):
    """可变壳：让 ModelRelayClient/ChatClient 通过 settings.model_relay.*
    读取运行时配置（它们内部只依赖这一层，不感知热更新）。"""
    view = runtime_relay.client_view()
    return type("_SettingsShell", (), {"model_relay": view})()


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    app = FastAPI(title="Local RAG Knowledge Base")
    app.state.platform_config = PlatformConfig.load()
    app.state.conformance_consumed_codes = set()
    app.state.conformance_sessions = {}

    if conformance_mode():
        from src.api.router_conformance import router as conformance_router
        from src.platform.redaction import install_redaction
        from src.platform.tasks import InMemoryNonceStore

        app.state.conformance_task_store = InMemoryNonceStore()
        install_redaction()
        app.include_router(platform_router)
        app.include_router(conformance_router)

        @app.exception_handler(AppError)
        async def _conformance_error_handler(request: Request, exc: AppError) -> JSONResponse:
            return JSONResponse(
                status_code=exc.status_code,
                content={"success": False, "error": {"code": exc.code, "message": exc.message}},
            )

        return app

    if settings is None:
        settings = Settings.load(Path(__file__).resolve().parent.parent.parent / "config.yaml")

    app.state.settings = settings
    app.state.control_plane_client = (
        ControlPlaneClient(app.state.platform_config)
        if app.state.platform_config.control_plane_base_url
        else None
    )

    # 视频解析 agent（quick-watch 集成；独立于数据库，本机 Python 执行）
    video_config = VideoConfig.load()
    app.state.video_manager = VideoTaskManager(video_config)

    if session_factory is None:
        from src.db.session import create_engine as db_create_engine
        database_url = settings.database.url.get_secret_value()
        if app.state.platform_config.database_url:
            # 平台生产运行：数据库凭据由 Controller 以 secrets 下发（规范 08 §12.1）
            database_url = app.state.platform_config.database_url
        engine = db_create_engine(
            database_url,
            pool_size=settings.database.pool_size,
            pool_recycle=settings.database.pool_recycle_seconds,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app.state.session_factory = session_factory

    # 运行时 model_relay 配置：默认来自 config.yaml，启动时从 DB 恢复已保存值
    runtime_relay = RuntimeModelRelay.from_frozen(settings.model_relay)
    app.state.runtime_relay = runtime_relay

    # 共享 HTTP client + 可变壳：模型客户端内部逻辑不变，只换配置来源
    model_client = httpx.AsyncClient(timeout=settings.model_relay.timeout_seconds)
    app.state.model_relay_client = ModelRelayClient(_make_settings_shell(runtime_relay), model_client)
    app.state.settings_shell = _make_settings_shell(runtime_relay)
    # Fallback for tests that skip the startup probe
    app.state.embedding_dimension: int = 0

    @app.on_event("startup")
    async def _startup_restore_and_probe() -> None:
        # 1. 从 DB 恢复已保存的模型配置（若有），并重置维度缓存
        try:
            async with app.state.session_factory() as session:
                stored = await session.scalar(select(ModelSetting).where(ModelSetting.id == 1))
            if stored is not None:
                runtime_relay.base_url = stored.base_url
                runtime_relay.api_key = stored.api_key
                runtime_relay.chat_model = stored.chat_model
                runtime_relay.embedding_model = stored.embedding_model
                # 保留原始形态：null 在 DB 中 = 与 chat 共用（客户端内部有 or 回退）
                runtime_relay.embedding_base_url = stored.embedding_base_url or ""
                runtime_relay.embedding_api_key = stored.embedding_api_key or ""
                app.state.embedding_dimension = 0
        except Exception:
            # DB 不可用（如离线启动）不阻断启动，保留 config.yaml 默认值
            pass

        # 2. 探测 embedding 维度（失败不阻断，KB 创建时会重试）
        try:
            app.state.embedding_dimension = await app.state.model_relay_client.probe_dimension()
        except Exception:
            # Probe failed (offline / invalid key) — routers_KB will re-probe on demand
            app.state.embedding_dimension = 0

        # 3. 启动文档入库 worker（消费 pending ingest job）
        chroma = ChromaRetrieval(
            session_factory,
            persist_dir=str(settings.rag.chroma_persist_dir),
            mode=settings.rag.chroma_mode,
            relay=app.state.model_relay_client,
        )
        app.state.ingest_chroma = chroma
        app.state.ingest_worker = IngestWorker(
            session_factory,
            upload_root=Path(settings.upload.root_dir),
            relay=app.state.model_relay_client,
            chroma=chroma,
        )
        app.state.ingest_worker.start()

    @app.on_event("shutdown")
    async def _close_model_client() -> None:
        worker = getattr(app.state, "ingest_worker", None)
        if worker is not None:
            await worker.stop()
        await app.state.model_relay_client._client.aclose()
        if app.state.control_plane_client is not None:
            await app.state.control_plane_client.close()
        video_manager = getattr(app.state, "video_manager", None)
        if video_manager is not None:
            video_manager.shutdown()

    app.include_router(auth_router)
    app.include_router(kb_router)
    app.include_router(docs_router)
    app.include_router(chat_router)
    app.include_router(users_router)
    app.include_router(settings_router)
    app.include_router(structured_router)
    app.include_router(platform_router)
    app.include_router(video_router)

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
