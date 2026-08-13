"""Conformance 模式 13 黑盒场景测试（规范 11 §7.1）。

门禁夹具以环境变量注入（TEST_*），只从环境读取，响应/日志不得回显。
Task JWT 夹具为合法结构（header.payload.signature，服务端不验签）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.platform.signing import sign_request


def _b64url(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_task_jwt(**overrides) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}))
    payload = {
        "iss": "tyt-rpa-controller",
        "aud": "rag-database",
        "env": "CONFORMANCE",
        "app": "rag-database",
        "executor": "rag-database-executor",
        "task": "task-0001",
        "attempt": 1,
        "claim": "claim-0001",
        "expiry": int(time.time()) + 3600,
        "status": "running",
        **overrides,
    }
    return f"{header}.{_b64url(json.dumps(payload))}.fakesignature"


@pytest.fixture()
def conformance_env(monkeypatch):
    monkeypatch.setenv("RPA_CONFORMANCE_MODE", "true")
    monkeypatch.setenv("RPA_APP_KEY", "rag-database")
    monkeypatch.setenv("RPA_RELEASE_VERSION", "conformance")
    monkeypatch.setenv("RPA_SOURCE_COMMIT", "unknown")
    monkeypatch.setenv("RPA_CONTROL_PLANE_PUBLIC_ORIGIN", "http://127.0.0.1")
    monkeypatch.setenv("TEST_AUTH_CODE", "auth-code-0001")
    monkeypatch.setenv("TEST_AUTH_STATE", "state-0001")
    monkeypatch.setenv("TEST_CODE_VERIFIER", "verifier-0001")
    monkeypatch.setenv("TEST_REDIRECT_URI", "http://testserver/")
    monkeypatch.setenv("TEST_ALLOWED_SESSION", "sess-allowed")
    monkeypatch.setenv("TEST_DENIED_SESSION", "sess-denied")
    monkeypatch.setenv("TEST_EXPIRED_SESSION", "sess-expired")
    monkeypatch.setenv("TEST_TASK_JWT", _make_task_jwt())
    monkeypatch.setenv("TEST_STALE_TASK_JWT", _make_task_jwt(status="stale"))
    monkeypatch.setenv("TEST_CANCELLED_TASK_JWT", _make_task_jwt(status="cancelled"))
    monkeypatch.setenv("TEST_HMAC_CLIENT_ID", "conformance-client")
    monkeypatch.setenv("TEST_HMAC_SECRET", "conformance-secret")
    monkeypatch.setenv("TEST_HMAC_SECRET_VERSION", "1")
    monkeypatch.setenv("TEST_SECRET_SENTINEL", "SENTINEL-TOP-SECRET-VALUE")
    return monkeypatch


@pytest.fixture()
async def client(conformance_env):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


def _signed_headers(
    *, method: str, path: str, body: bytes, idempotency_key: str = "", nonce: str | None = None,
    timestamp_ms: int | None = None, client_id: str = "conformance-client",
    secret: str = "conformance-secret",
) -> dict[str, str]:
    signed = sign_request(
        method=method,
        path_with_query=path,
        body=body,
        client_id=client_id,
        secret=secret,
        secret_version="1",
        idempotency_key=idempotency_key,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
    )
    return {**signed.headers, "Content-Type": "application/json"}


async def test_sso_code_once(client: AsyncClient) -> None:
    body = {
        "code": "auth-code-0001",
        "state": "state-0001",
        "codeVerifier": "verifier-0001",
        "redirectUri": "http://testserver/",
    }
    first = await client.post("/platform/sso/bootstrap", data=body)
    assert first.status_code == 200
    second = await client.post("/platform/sso/bootstrap", data=body)
    assert second.status_code == 401


async def test_sso_rejects_bad_state(client: AsyncClient) -> None:
    response = await client.post(
        "/platform/sso/bootstrap",
        data={
            "code": "auth-code-0001",
            "state": "wrong-state",
            "codeVerifier": "verifier-0001",
            "redirectUri": "http://testserver/",
        },
    )
    assert response.status_code == 401


async def test_session_expired(client: AsyncClient) -> None:
    response = await client.get(
        "/platform/session", headers={"X-Test-Session": "sess-expired"}
    )
    assert response.status_code == 401


async def test_permission_allowed(client: AsyncClient) -> None:
    response = await client.get(
        "/api/rpa/conformance/permissions", headers={"X-Test-Session": "sess-allowed"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["userId"] == "2068630213954715649"
    assert "rag-database:knowledge-base:manage" in data["permissions"]


async def test_permission_denied(client: AsyncClient) -> None:
    response = await client.get(
        "/api/rpa/conformance/permissions", headers={"X-Test-Session": "sess-denied"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_task_jwt_valid(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-1", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()
    headers = _signed_headers(method="POST", path="/api/rpa/tasks", body=raw, idempotency_key="t-1")
    headers["X-Task-Jwt"] = os.environ["TEST_TASK_JWT"]
    response = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert response.status_code == 202
    assert response.json()["data"]["taskId"]


async def test_task_jwt_stale(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-2", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()
    headers = _signed_headers(method="POST", path="/api/rpa/tasks", body=raw, idempotency_key="t-2")
    headers["X-Task-Jwt"] = os.environ["TEST_STALE_TASK_JWT"]
    response = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "STALE_CLAIM"


async def test_task_jwt_cancelled(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-3", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()
    headers = _signed_headers(method="POST", path="/api/rpa/tasks", body=raw, idempotency_key="t-3")
    headers["X-Task-Jwt"] = os.environ["TEST_CANCELLED_TASK_JWT"]
    response = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AGENT_CANCELLED"


async def test_hmac_valid(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-4", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()
    headers = _signed_headers(method="POST", path="/api/rpa/tasks", body=raw, idempotency_key="h-1")
    headers["X-Task-Jwt"] = os.environ["TEST_TASK_JWT"]
    response = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert response.status_code == 202


async def test_hmac_nonce_replay(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-5", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()
    headers = _signed_headers(method="POST", path="/api/rpa/tasks", body=raw, nonce="fixed-nonce-1")
    headers["X-Task-Jwt"] = os.environ["TEST_TASK_JWT"]
    first = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert first.status_code == 202
    second = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "HMAC_REPLAY"


async def test_hmac_clock_skew(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-6", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()
    old_ms = (time.time_ns() // 1_000_000) - 10 * 60 * 1000  # 偏移 10 分钟
    headers = _signed_headers(
        method="POST", path="/api/rpa/tasks", body=raw, timestamp_ms=old_ms
    )
    headers["X-Task-Jwt"] = os.environ["TEST_TASK_JWT"]
    response = await client.post("/api/rpa/tasks", content=raw, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HMAC_CLOCK_SKEW"


async def test_task_idempotency(client: AsyncClient) -> None:
    payload = {"businessRequestId": "REQ-7", "taskType": "DOCUMENT_INGEST"}
    raw = json.dumps(payload).encode()

    def _request_headers() -> dict[str, str]:
        headers = _signed_headers(
            method="POST", path="/api/rpa/tasks", body=raw, idempotency_key="idem-1"
        )
        headers["X-Task-Jwt"] = os.environ["TEST_TASK_JWT"]
        return headers

    first = await client.post("/api/rpa/tasks", content=raw, headers=_request_headers())
    second = await client.post("/api/rpa/tasks", content=raw, headers=_request_headers())
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["taskId"] == second.json()["data"]["taskId"]


async def test_result_idempotency(client: AsyncClient) -> None:
    payload = {
        "taskId": "task-9",
        "businessRequestId": "REQ-8",
        "status": "SUCCEEDED",
    }
    raw = json.dumps(payload).encode()
    headers = _signed_headers(method="POST", path="/api/rpa/tasks/results", body=raw, idempotency_key="r-1")
    first = await client.post("/api/rpa/tasks/results", content=raw, headers=headers)
    second = await client.post("/api/rpa/tasks/results", content=raw, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["resultId"] == second.json()["data"]["resultId"]


async def test_log_redaction(caplog, conformance_env) -> None:
    """夹具密钥不得出现在日志（LOG_REDACTION 场景）。"""
    from src.platform.redaction import install_redaction

    install_redaction()
    with caplog.at_level("INFO"):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(
                "/platform/health",
                headers={"X-Test-Sentinel": os.environ["TEST_SECRET_SENTINEL"]},
            )
            assert response.status_code == 200
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert os.environ["TEST_SECRET_SENTINEL"] not in combined
