"""Conformance 模式下的 13 黑盒场景端点（规范 11 §7.1）。

无数据库、无模型服务：所有状态保存在进程内 Map（重启即清空，符合门禁语义）。
覆盖场景：SSO_CODE_ONCE / SESSION_EXPIRED / PERMISSION_ALLOWED /
PERMISSION_DENIED / TASK_JWT_VALID / TASK_JWT_STALE / TASK_JWT_CANCELLED /
HMAC_VALID / HMAC_NONCE_REPLAY / HMAC_CLOCK_SKEW / TASK_IDEMPOTENCY /
RESULT_IDEMPOTENCY / LOG_REDACTION。
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, StrictStr

from src.api.dependencies import require_permission
from src.platform.config import conformance_mode
from src.platform.principal import (
    PERMISSION_KB_MANAGE,
    PERMISSION_PROJECT_VIEW,
    ProjectPrincipal,
)
from src.platform.tasks import InMemoryNonceStore, validate_task_jwt, verify_hmac_request
from src.shared.errors import AppError

router = APIRouter(prefix="/api/rpa", tags=["conformance"])


def _assert_conformance() -> None:
    if not conformance_mode():
        raise AppError("FORBIDDEN", "该端点仅在 Conformance 模式可用", status_code=403)


def _hmac_material() -> tuple[str, str]:
    return (
        os.environ.get("TEST_HMAC_CLIENT_ID", "conformance-client"),
        os.environ.get("TEST_HMAC_SECRET", "conformance-secret"),
    )


class TaskCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    businessRequestId: StrictStr = Field(min_length=1)
    taskType: StrictStr = Field(min_length=1)
    contextRef: StrictStr | None = None


@router.post("/tasks", status_code=202)
async def create_task(
    body: TaskCreateRequest,
    request: Request,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict[str, object]:
    """Task 创建：HMAC 签名 + Task JWT + 幂等（conformance 进程内 Map）。"""
    _assert_conformance()
    client_id, secret = _hmac_material()
    store: InMemoryNonceStore = request.app.state.conformance_task_store

    raw_body = await request.body()
    idempotency_key = verify_hmac_request(
        method="POST",
        path_with_query=request.url.path,
        body=raw_body,
        headers={key: value for key, value in request.headers.items()},
        expected_client_id=client_id,
        expected_secret=secret,
        nonce_store=store,
    )

    task_jwt = request.headers.get("X-Task-Jwt", "")
    if not task_jwt:
        raise AppError("UNAUTHORIZED", "缺少 Task JWT", status_code=401)
    validate_task_jwt(
        task_jwt,
        expected_env=os.environ.get("RPA_ENVIRONMENT", "CONFORMANCE"),
        expected_app=os.environ.get("RPA_APP_KEY", "rag-database"),
        conformance=True,
    )

    if idempotency_key:
        existing = store.get_idempotent(idempotency_key)
        if existing is not None:
            return {"success": True, "data": existing}
    task_id = str(uuid.uuid4())
    result = {
        "taskId": task_id,
        "attempt": 1,
        "status": "PENDING",
        "businessRequestId": body.businessRequestId,
    }
    if idempotency_key:
        store.put_idempotent(idempotency_key, result)
    return {"success": True, "data": result}


class TaskResultRequest(BaseModel):
    model_config = {"extra": "forbid"}
    taskId: StrictStr = Field(min_length=1)
    businessRequestId: StrictStr = Field(min_length=1)
    status: StrictStr = Field(min_length=1)
    payload: dict[str, object] | None = None


@router.post("/tasks/results")
async def submit_result(request: Request, body: TaskResultRequest) -> dict[str, object]:
    """结果提交：重复提交返回同一 resultId（RESULT_IDEMPOTENCY）。"""
    _assert_conformance()
    store: InMemoryNonceStore = request.app.state.conformance_task_store
    key = f"{body.businessRequestId}:{body.taskId}"
    existing = store.get_result(key)
    if existing is not None:
        return {"success": True, "data": existing}
    result = {
        "resultId": str(uuid.uuid4()),
        "taskId": body.taskId,
        "businessRequestId": body.businessRequestId,
        "status": body.status,
    }
    store.put_result(key, result)
    return {"success": True, "data": result}


@router.get("/conformance/permissions")
async def conformance_permissions(
    principal: ProjectPrincipal = Depends(
        require_permission(PERMISSION_KB_MANAGE)
    ),
) -> dict[str, object]:
    """业务 stub：要求 knowledge-base:manage 权限（PERMISSION_ALLOWED/DENIED 场景）。"""
    _assert_conformance()
    return {
        "success": True,
        "data": {
            "tenantId": principal.tenant_id,
            "userId": principal.external_user_id,
            "permissions": sorted(principal.permissions),
            "hasProjectView": principal.has_permission(PERMISSION_PROJECT_VIEW),
        },
    }
