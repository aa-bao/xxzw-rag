from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.platform.client import validate_same_origin_route
from src.platform.config import conformance_mode
from src.platform.identity import PlatformIdentity
from src.platform.sessions import PLATFORM_SESSION_COOKIE, PlatformSessionService
from src.shared.errors import AppError

router = APIRouter(prefix="/platform", tags=["platform"])


def conformance_identity_for_session(
    token: str, app_state: object
) -> PlatformIdentity | None:
    """Conformance 模式按门禁夹具解析会话身份；不认识的 token 返回 None（401）。"""
    identity = app_state.conformance_sessions.get(token)
    if identity is not None:
        return identity
    if token == os.environ.get("TEST_ALLOWED_SESSION"):
        return _conformance_identity()
    if token == os.environ.get("TEST_DENIED_SESSION"):
        return _conformance_identity(allowed=False)
    return None


def _release_metadata() -> dict[str, str]:
    """Return only Controller-provided, non-secret release identifiers."""
    environment = os.environ.get("RPA_ENVIRONMENT", "LOCAL")
    if conformance_mode():
        environment = "CONFORMANCE"
    return {
        "appKey": os.environ.get("RPA_APP_KEY", "rag-database"),
        "environment": environment,
        "version": os.environ.get("RPA_RELEASE_VERSION", "dev"),
        "sourceCommit": os.environ.get("RPA_SOURCE_COMMIT", "unknown"),
        "configVersion": os.environ.get("RPA_CONFIG_VERSION", ""),
    }


@router.get("/health")
async def health() -> dict[str, str]:
    """Process liveness probe; intentionally does not call dependencies."""
    return {"status": "healthy"}


@router.get("/readiness")
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe covering the mandatory database dependency."""
    if conformance_mode():
        return JSONResponse(status_code=200, content={"status": "ready"})
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "DEPENDENCY_UNAVAILABLE"},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})


@router.get("/version")
async def version() -> dict[str, str]:
    return _release_metadata()


def _conformance_identity(*, allowed: bool = True) -> PlatformIdentity:
    permissions = [
        "rag-database:project:view",
        "rag-database:knowledge-base:manage",
        "rag-database:chat:use",
        "rag-database:settings:manage",
    ] if allowed else ["rag-database:project:view"]
    return PlatformIdentity.model_validate(
        {
            "tenantId": "000000",
            "userId": "2068630213954715649",
            "username": "conformance-user",
            "displayName": "Conformance User",
            "departmentId": "2068630213954715650",
            "departmentName": "Conformance",
            "departmentCategory": "",
            "roles": ["superadmin"] if allowed else ["operator"],
            "permissions": permissions,
            "dataScope": {"mode": "ALL", "departmentIds": [], "userIds": []},
            "issuedAt": "2026-08-12 00:00:00",
        }
    )


@router.post("/sso/bootstrap")
async def sso_bootstrap(
    request: Request,
    response: Response,
    code: str = Form(...),
    state: str = Form(...),
    code_verifier: str = Form(..., alias="codeVerifier"),
    redirect_uri: str = Form(..., alias="redirectUri"),
    route: str | None = Form(default=None),
) -> dict[str, object]:
    safe_route = validate_same_origin_route(route)
    if conformance_mode():
        expected = os.environ.get("TEST_AUTH_CODE")
        if not expected or code != expected:
            raise AppError("UNAUTHORIZED", "平台授权无效或已使用", status_code=401)
        if state != os.environ.get("TEST_AUTH_STATE"):
            raise AppError("UNAUTHORIZED", "平台授权 state 无效", status_code=401)
        if code_verifier != os.environ.get("TEST_CODE_VERIFIER"):
            raise AppError("UNAUTHORIZED", "平台 PKCE 校验失败", status_code=401)
        if redirect_uri != os.environ.get("TEST_REDIRECT_URI"):
            raise AppError("UNAUTHORIZED", "平台重定向地址无效", status_code=401)
        consumed: set[str] = request.app.state.conformance_consumed_codes
        if code in consumed:
            raise AppError("UNAUTHORIZED", "平台授权已使用", status_code=401)
        consumed.add(code)
        token = os.environ.get("TEST_ALLOWED_SESSION", "conformance-allowed-session")
        request.app.state.conformance_sessions[token] = _conformance_identity()
    else:
        identity = await request.app.state.control_plane_client.exchange(
            code=code,
            state=state,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )
        async with request.app.state.session_factory() as db:
            token, _ = await PlatformSessionService.issue(db, identity)
    response.set_cookie(
        key=PLATFORM_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=not conformance_mode(),
        samesite="lax",
        max_age=30 * 60,
        path="/",
    )
    return {"success": True, "data": {"route": safe_route}}


@router.get("/session")
async def platform_session(
    request: Request,
    token: str | None = Cookie(default=None, alias=PLATFORM_SESSION_COOKIE),
) -> dict[str, object]:
    if conformance_mode():
        token = token or request.headers.get("X-Test-Session")
        if token == os.environ.get("TEST_EXPIRED_SESSION"):
            raise AppError("UNAUTHORIZED", "项目会话已过期", status_code=401)
        identity = conformance_identity_for_session(token or "", request.app.state)
        if identity is None:
            raise AppError("UNAUTHORIZED", "项目会话无效", status_code=401)
    else:
        async with request.app.state.session_factory() as db:
            authenticated = await PlatformSessionService.authenticate(db, token)
        if authenticated is None:
            raise AppError("UNAUTHORIZED", "项目会话无效或已过期", status_code=401)
        identity = authenticated.identity
    return {"success": True, "data": identity.model_dump()}


@router.post("/logout")
async def platform_logout(
    request: Request,
    response: Response,
    token: str | None = Cookie(default=None, alias=PLATFORM_SESSION_COOKIE),
) -> dict[str, object]:
    if conformance_mode():
        request.app.state.conformance_sessions.pop(token, None)
    else:
        async with request.app.state.session_factory() as db:
            await PlatformSessionService.revoke(db, token)
    response.delete_cookie(PLATFORM_SESSION_COOKIE, path="/")
    return {"success": True, "data": None}
