"""模型配置管理（/api/settings/models）——仅管理员可访问。

配置存储在运行时内存（app.state.runtime_relay），由 PUT 热更新并持久化
到 rag_model_setting 表；服务重启后启动时从 DB 恢复覆盖 config.yaml 默认值。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import _session_factory, require_permission
from src.db.repositories import ModelSettingRepository
from src.models.client import ModelError
from src.platform.principal import PERMISSION_SETTINGS_MANAGE, ProjectPrincipal
from src.shared.errors import AppError
from src.shared.runtime import RuntimeModelRelay

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateModelSettingsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    base_url: str | None = None
    chat_model: str | None = None
    embedding_model: str | None = None
    # null = 与 chat 共用；非空 = 更新；缺省 = 不更新（靠 model_fields_set 区分）
    embedding_base_url: str | None = None
    # 空字符串 = 不更新（保留原值）；非空 = 更新
    api_key: str | None = None
    embedding_api_key: str | None = None


class TestModelSettingsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    # 测试目标：chat 试调 chat/completions；embedding 试调 embeddings 探针
    mode: Literal["chat", "embedding"] = "embedding"
    base_url: str = Field(min_length=1)
    chat_model: str | None = None
    api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None


def _serialize(runtime: RuntimeModelRelay) -> dict[str, object]:
    return {
        "success": True,
        "data": runtime.to_dict(),
    }


def _apply_update(runtime: RuntimeModelRelay, body: UpdateModelSettingsRequest) -> bool:
    """按更新语义改运行时配置；有实际变化返回 True。"""
    changed = False
    if body.base_url is not None and body.base_url != runtime.base_url:
        runtime.base_url = body.base_url
        changed = True
    if body.chat_model is not None and body.chat_model != runtime.chat_model:
        runtime.chat_model = body.chat_model
        changed = True
    if body.embedding_model is not None and body.embedding_model != runtime.embedding_model:
        runtime.embedding_model = body.embedding_model
        changed = True
    # 显式 null = 与 chat 共用；非空字符串 = 更新；缺省（未出现在请求体）= 不更新
    if "embedding_base_url" in body.model_fields_set:
        new_value = body.embedding_base_url or ""
        if new_value != runtime.embedding_base_url:
            runtime.embedding_base_url = new_value
            changed = True
    # 空字符串 = 保留原值；非空 = 更新
    if body.api_key is not None and body.api_key != "" and body.api_key != runtime.api_key:
        runtime.api_key = body.api_key
        changed = True
    if (
        body.embedding_api_key is not None
        and body.embedding_api_key != ""
        and body.embedding_api_key != runtime.embedding_api_key
    ):
        runtime.embedding_api_key = body.embedding_api_key
        changed = True
    return changed


@router.get("/models")
async def get_model_settings(
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_SETTINGS_MANAGE)),
) -> dict[str, object]:
    runtime: RuntimeModelRelay = request.app.state.runtime_relay
    return _serialize(runtime)


@router.put("/models")
async def update_model_settings(
    body: UpdateModelSettingsRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_SETTINGS_MANAGE)),
    db: AsyncSession = Depends(_session_factory),
) -> dict[str, object]:
    runtime: RuntimeModelRelay = request.app.state.runtime_relay

    # 校验：提供且非空字符串的字段必须非空
    if body.base_url is not None and not body.base_url.strip():
        raise AppError("BASE_URL_REQUIRED", "base_url 不能为空")
    if body.chat_model is not None and not body.chat_model.strip():
        raise AppError("CHAT_MODEL_REQUIRED", "chat_model 不能为空")
    if body.embedding_model is not None and not body.embedding_model.strip():
        raise AppError("EMBEDDING_MODEL_REQUIRED", "embedding_model 不能为空")
    if (
        "embedding_base_url" in body.model_fields_set
        and body.embedding_base_url is not None
        and not body.embedding_base_url.strip()
    ):
        raise AppError("EMBEDDING_BASE_URL_INVALID", "embedding_base_url 不能为空")

    if not _apply_update(runtime, body):
        raise AppError("NO_UPDATE_FIELDS", "没有需要更新的字段", status_code=400)

    # 持久化：与 chat 共用的字段回写 NULL
    repo = ModelSettingRepository(db)
    await repo.upsert(
        {
            "base_url": runtime.base_url,
            "api_key": runtime.api_key,
            "chat_model": runtime.chat_model,
            "embedding_model": runtime.embedding_model,
            "embedding_base_url": runtime.embedding_base_url or None,
            "embedding_api_key": runtime.embedding_api_key or None,
        }
    )

    # 重置维度缓存：下次创建 KB 时按新模型重新 probe
    app = request.app
    if app.state.embedding_dimension != 0:
        app.state.embedding_dimension = 0

    return _serialize(runtime)


@router.post("/models/test")
async def test_model_settings(
    body: TestModelSettingsRequest,
    request: Request,
    principal: ProjectPrincipal = Depends(require_permission(PERMISSION_SETTINGS_MANAGE)),
) -> dict[str, object]:
    """用传入配置试连模型服务；失败返回 200 包裹的错误（不抛 500）。"""
    runtime: RuntimeModelRelay = request.app.state.runtime_relay
    app = request.app

    candidate = runtime.copy()
    if body.api_key is not None and body.api_key != "":
        candidate.api_key = body.api_key
    if body.embedding_api_key is not None and body.embedding_api_key != "":
        candidate.embedding_api_key = body.embedding_api_key
    if body.embedding_base_url is not None:
        candidate.embedding_base_url = body.embedding_base_url
    candidate.base_url = body.base_url
    if body.chat_model is not None and body.chat_model != "":
        candidate.chat_model = body.chat_model
    if body.embedding_model is not None and body.embedding_model != "":
        candidate.embedding_model = body.embedding_model

    shell = candidate.client_view()
    probe_client = app.state.model_relay_client.__class__(
        type("_SettingsShell", (), {"model_relay": shell})(),
        app.state.model_relay_client.client,
    )

    try:
        if body.mode == "chat":
            # 试调一次 chat/completions（最小请求，期望非 200 时 ModelError 报错）
            from src.models.llm import ChatClient

            chat_client = ChatClient(
                type("_SettingsShell", (), {"model_relay": shell})(),
                app.state.model_relay_client.client,
            )
            messages = [{"role": "user", "content": "hi"}]
            async for _ in chat_client.stream(messages):
                break
            return {"success": True, "data": {"ok": True}}
        dimension = await probe_client.probe_dimension()
    except Exception as exc:
        message = exc.message if isinstance(exc, ModelError) else str(exc) or "连接失败"
        return {"success": True, "data": {"ok": False, "message": message}}
    return {"success": True, "data": {"ok": True, "dimension": dimension}}
