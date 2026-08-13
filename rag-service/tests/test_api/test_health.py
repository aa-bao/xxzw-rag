from importlib import import_module
from pathlib import Path
import subprocess

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock


def _write_test_secrets(root: Path) -> None:
    """写入 TEST 模式所需的最小 secret root（真实自签名 CA）。"""
    for name, value in {
        "RPA_CONTROL_PLANE_BASE_URL": "http://127.0.0.1",
        "RPA_PROJECT_SERVICE_CLIENT_ID": "test-client",
        "RPA_PROJECT_SERVICE_SECRET": "test-secret",
        "RPA_PROJECT_SERVICE_SECRET_VERSION": "1",
    }.items():
        (root / name).write_text(value, encoding="utf-8")
    key_path = root / "ca-key.pem"
    cert_path = root / "CONTROL_PLANE_TLS_CA_CERTIFICATE"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "30", "-nodes", "-subj", "/CN=test-ca",
        ],
        check=True,
        capture_output=True,
    )


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


async def test_platform_health_and_version_are_dependency_free(monkeypatch, tmp_path) -> None:
    create_app = _load_create_app()
    monkeypatch.setenv("RPA_APP_KEY", "rag-database")
    monkeypatch.setenv("RPA_ENVIRONMENT", "TEST")
    monkeypatch.setenv("RPA_RELEASE_VERSION", "2026.08.12.1")
    monkeypatch.setenv("RPA_SOURCE_COMMIT", "abc123")
    monkeypatch.setenv("RPA_CONFIG_VERSION", "cfg-7")
    # TEST/PRODUCTION 模式按规范 fail-closed：缺少 Controller secrets 必须拒绝启动。
    # 这里提供临时 secret root，验证 health/version 探针本身不依赖数据库或模型服务。
    _write_test_secrets(tmp_path)
    monkeypatch.setenv("RPA_SECRET_ROOT", str(tmp_path))

    settings = MagicMock()
    settings.model_relay.timeout_seconds = 1
    settings.model_relay.base_url = "http://model.invalid"
    settings.model_relay.api_key.get_secret_value.return_value = "test"
    settings.model_relay.embedding_model = "test"
    settings.model_relay.chat_model = "test"
    settings.model_relay.embedding_base_url = "http://model.invalid"
    settings.model_relay.embedding_api_key.get_secret_value.return_value = "test"
    app = create_app(settings=settings, session_factory=MagicMock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        health_response = await client.get("/platform/health")
        version_response = await client.get("/platform/version")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "healthy"}
    assert version_response.json() == {
        "appKey": "rag-database",
        "environment": "TEST",
        "version": "2026.08.12.1",
        "sourceCommit": "abc123",
        "configVersion": "cfg-7",
    }


async def test_platform_readiness_reports_database_state(monkeypatch) -> None:
    create_app = _load_create_app()
    settings = MagicMock()
    settings.model_relay.timeout_seconds = 1
    settings.model_relay.base_url = "http://model.invalid"
    settings.model_relay.api_key.get_secret_value.return_value = "test"
    settings.model_relay.embedding_model = "test"
    settings.model_relay.chat_model = "test"
    settings.model_relay.embedding_base_url = "http://model.invalid"
    settings.model_relay.embedding_api_key.get_secret_value.return_value = "test"

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session_factory = MagicMock(return_value=session)
    app = create_app(settings=settings, session_factory=session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        ready_response = await client.get("/platform/readiness")
        session.execute.side_effect = RuntimeError("database unavailable")
        unavailable_response = await client.get("/platform/readiness")

    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ready"}
    assert unavailable_response.status_code == 503
    assert unavailable_response.json() == {
        "status": "not_ready",
        "reason": "DEPENDENCY_UNAVAILABLE",
    }
