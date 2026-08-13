from __future__ import annotations

import json
import ssl
from urllib.parse import urlsplit

import httpx

from src.platform.config import PlatformConfig
from src.platform.identity import PlatformIdentity
from src.platform.signing import sign_request
from src.shared.errors import AppError


# TODO(platform-contract): 以下适配器路径尚未从本地主系统 OpenAPI/契约验证。
# 规范只冻结了 SSO 流程与 HMAC 规则，未在本地规范中给出兑换 API 路径。
# 接入真实主系统前必须按主系统 contracts/ 确认并纠正，不得把猜测路径宣称为权威契约。
SSO_EXCHANGE_PATH = "/api/rpa/project-applications/sso/exchange"
SESSION_REFRESH_PATH = "/api/rpa/project-applications/sso/session"


class ControlPlaneClient:
    def __init__(self, config: PlatformConfig) -> None:
        self.config = config
        verify: bool | ssl.SSLContext = True
        if config.control_plane_ca_path is not None:
            verify = ssl.create_default_context(cafile=str(config.control_plane_ca_path))
        self._client = httpx.AsyncClient(base_url=config.control_plane_base_url, verify=verify, timeout=15)

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        signed = sign_request(
            method="POST",
            path_with_query=path,
            body=body,
            client_id=self.config.service_client_id,
            secret=self.config.service_secret,
            secret_version=self.config.service_secret_version,
            signature_version=self.config.signature_version,
        )
        try:
            response = await self._client.post(
                path,
                content=body,
                headers={**signed.headers, "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AppError(
                "DEPENDENCY_UNAVAILABLE", "主系统身份服务暂不可用", status_code=503
            ) from exc
        if response.status_code in {401, 403, 409}:
            raise AppError("UNAUTHORIZED", "平台授权无效或已使用", status_code=401)
        if response.status_code >= 500:
            raise AppError("DEPENDENCY_UNAVAILABLE", "主系统身份服务暂不可用", status_code=503)
        if response.status_code >= 400:
            raise AppError("VALIDATION_FAILED", "平台授权请求无效", status_code=400)
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        if not isinstance(data, dict):
            raise AppError("DEPENDENCY_UNAVAILABLE", "主系统身份响应格式错误", status_code=503)
        return data

    async def exchange(
        self, *, code: str, state: str, code_verifier: str, redirect_uri: str
    ) -> PlatformIdentity:
        data = await self._post(
            SSO_EXCHANGE_PATH,
            {
                "appKey": self.config.app_key,
                "code": code,
                "state": state,
                "codeVerifier": code_verifier,
                "redirectUri": redirect_uri,
            },
        )
        return PlatformIdentity.model_validate(data)

    async def refresh(self, identity: PlatformIdentity) -> PlatformIdentity:
        data = await self._post(
            SESSION_REFRESH_PATH,
            {
                "appKey": self.config.app_key,
                "tenantId": identity.tenantId,
                "userId": identity.userId,
            },
        )
        refreshed = PlatformIdentity.model_validate(data)
        if refreshed.tenantId != identity.tenantId or refreshed.userId != identity.userId:
            raise AppError("UNAUTHORIZED", "平台身份已失效", status_code=401)
        return refreshed


def validate_same_origin_route(route: str | None) -> str:
    if not route:
        return "/kb"
    parsed = urlsplit(route)
    if (
        not route.startswith("/")
        or route.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in route
        or ".." in parsed.path.split("/")
    ):
        raise AppError("VALIDATION_FAILED", "route 必须是同源相对路径")
    return route
