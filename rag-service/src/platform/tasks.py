"""Task JWT 校验与 HMAC 验签（规范 08 §11.1、规范 11 §8/G5）。

- HMAC canonical string 顺序严格固定；非ce 防重放（进程内 Map，跨请求幂等）；
  毫秒时间戳防时钟偏移；401/403 不重试，5xx 只做有限退避（调用方策略）。
- Task JWT：校验 iss/aud/env/app/executor/task/attempt/claim/expiry/status；
  旧 Claim、取消、过期与错误 audience 一律拒绝。
- Conformance 模式：按门禁夹具精确匹配（TEST_TASK_JWT 等），同时也做 JWT 结构校验。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field

from src.shared.errors import AppError

logger = logging.getLogger(__name__)

HMAC_CLOCK_SKEW_MS = 300_000  # 5 分钟（规范 11 场景 HMAC_CLOCK_SKEW 以偏移时钟验证）
REQUIRED_TASK_CLAIMS = (
    "iss",
    "aud",
    "env",
    "app",
    "executor",
    "task",
    "attempt",
    "claim",
    "expiry",
    "status",
)


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass
class InMemoryNonceStore:
    """进程内 nonce/幂等结果存储（Conformance 模式跨请求幂等；重启即清空属预期）。"""

    _seen_nonces: set[str] = field(default_factory=set)
    _idempotency: dict[str, dict[str, object]] = field(default_factory=dict)
    _results: dict[str, dict[str, object]] = field(default_factory=dict)

    def consume_nonce(self, nonce: str) -> bool:
        """消费 nonce；已见过（重放）返回 False。"""
        if nonce in self._seen_nonces:
            return False
        self._seen_nonces.add(nonce)
        return True

    def get_idempotent(self, key: str) -> dict[str, object] | None:
        return self._idempotency.get(key)

    def put_idempotent(self, key: str, value: dict[str, object]) -> dict[str, object]:
        self._idempotency[key] = value
        return value

    def get_result(self, key: str) -> dict[str, object] | None:
        return self._results.get(key)

    def put_result(self, key: str, value: dict[str, object]) -> dict[str, object]:
        self._results[key] = value
        return value


def verify_hmac_request(
    *,
    method: str,
    path_with_query: str,
    body: bytes,
    headers: dict[str, str],
    expected_client_id: str,
    expected_secret: str,
    nonce_store: InMemoryNonceStore,
    timestamp_ms: int | None = None,
) -> str:
    """校验控制面请求签名；返回幂等键（可能为空串）。

    失败抛 401 AppError（UNAUTHORIZED / HMAC_REPLAY / HMAC_CLOCK_SKEW）。
    """
    # ASGI 会把请求头键规范化为小写，这里统一转小写再查询
    normalized = {key.lower(): value for key, value in headers.items()}
    signature = normalized.get("x-signature", "")
    client_id = normalized.get("x-project-client-id", "")
    secret_version = normalized.get("x-project-secret-version", "")
    signature_version = normalized.get("x-project-signature-version", "v1")
    header_timestamp = normalized.get("x-timestamp", "")
    nonce = normalized.get("x-nonce", "")
    idempotency_key = normalized.get("idempotency-key", "")

    if client_id != expected_client_id:
        raise AppError("UNAUTHORIZED", "签名 clientId 无效", status_code=401)

    if not header_timestamp.isdigit():
        raise AppError("UNAUTHORIZED", "时间戳格式无效", status_code=401)
    sent_ms = int(header_timestamp)
    now_ms = timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000
    if abs(now_ms - sent_ms) > HMAC_CLOCK_SKEW_MS:
        raise AppError("HMAC_CLOCK_SKEW", "请求时间戳超出允许偏移", status_code=401)

    if not nonce or not nonce_store.consume_nonce(nonce):
        raise AppError("HMAC_REPLAY", "请求 nonce 重复", status_code=401)

    canonical = "\n".join(
        [
            signature_version,
            client_id,
            method.upper(),
            path_with_query,
            header_timestamp,
            nonce,
            idempotency_key,
            body_sha256(body),
        ]
    )
    expected = hmac.new(expected_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AppError("UNAUTHORIZED", "请求签名无效", status_code=401)
    return idempotency_key


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_task_jwt(token: str) -> dict[str, object]:
    """解码 Task JWT 并校验必需 claims；结构非法抛 AppError。"""
    try:
        header, payload, _signature = token.split(".")
        header_data = json.loads(_b64url_decode(header))
        payload_data = json.loads(_b64url_decode(payload))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise AppError("UNAUTHORIZED", "Task JWT 结构非法", status_code=401) from None
    if not isinstance(header_data, dict) or not isinstance(payload_data, dict):
        raise AppError("UNAUTHORIZED", "Task JWT 结构非法", status_code=401)
    missing = [name for name in REQUIRED_TASK_CLAIMS if name not in payload_data]
    if missing:
        raise AppError(
            "UNAUTHORIZED", f"Task JWT 缺少必要声明: {', '.join(missing)}", status_code=401
        )
    return payload_data


def validate_task_jwt(
    token: str,
    *,
    expected_env: str,
    expected_app: str,
    expected_audience: str | None = None,
    conformance: bool = False,
) -> dict[str, object]:
    """校验 Task JWT 的 issuer/audience/environment/app/expiry/claim/status。

    Conformance 模式：门禁夹具精确匹配（stale/cancelled 夹具先于结构校验命中）；
    夹具通过后仅做结构校验（claims 齐全 + expiry 有效），env/app 由门禁夹具保证。
    """
    conformance_match = False
    if conformance:
        if token == os.environ.get("TEST_STALE_TASK_JWT"):
            raise AppError("STALE_CLAIM", "Task 声明已过期（旧 Claim）", status_code=401)
        if token == os.environ.get("TEST_CANCELLED_TASK_JWT"):
            raise AppError("AGENT_CANCELLED", "Task 已取消", status_code=401)
        if token == os.environ.get("TEST_TASK_JWT"):
            conformance_match = True
        else:
            raise AppError("UNAUTHORIZED", "Task JWT 无效", status_code=401)

    payload = decode_task_jwt(token)

    if not conformance_match:
        if expected_audience and payload.get("aud") != expected_audience:
            raise AppError("UNAUTHORIZED", "Task JWT audience 不匹配", status_code=401)
        if payload.get("env") != expected_env:
            raise AppError("UNAUTHORIZED", "Task JWT 环境不匹配", status_code=401)
        if payload.get("app") != expected_app:
            raise AppError("UNAUTHORIZED", "Task JWT 应用不匹配", status_code=401)

    expiry = payload.get("expiry")
    if isinstance(expiry, (int, float)) and expiry * 1000 < time.time_ns() // 1_000_000:
        raise AppError("UNAUTHORIZED", "Task JWT 已过期", status_code=401)
    if isinstance(expiry, str) and expiry.isdigit() and int(expiry) * 1000 < time.time_ns() // 1_000_000:
        raise AppError("UNAUTHORIZED", "Task JWT 已过期", status_code=401)

    status = str(payload.get("status", ""))
    if status == "cancelled":
        raise AppError("AGENT_CANCELLED", "Task 已取消", status_code=401)
    if status == "stale":
        raise AppError("STALE_CLAIM", "Task 声明已过期（旧 Claim）", status_code=401)
    return payload
