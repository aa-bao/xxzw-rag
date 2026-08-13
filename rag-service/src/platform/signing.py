from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class SignedHeaders:
    headers: dict[str, str]
    timestamp_ms: int
    nonce: str


def sign_request(
    *,
    method: str,
    path_with_query: str,
    body: bytes,
    client_id: str,
    secret: str,
    secret_version: str,
    signature_version: str = "v1",
    idempotency_key: str = "",
    timestamp_ms: int | None = None,
    nonce: str | None = None,
) -> SignedHeaders:
    timestamp_ms = timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000
    nonce = nonce or secrets.token_urlsafe(18)
    canonical = "\n".join(
        [
            signature_version,
            client_id,
            method.upper(),
            path_with_query,
            str(timestamp_ms),
            nonce,
            idempotency_key,
            body_sha256(body),
        ]
    )
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return SignedHeaders(
        headers={
            "X-Project-Client-Id": client_id,
            "X-Project-Secret-Version": secret_version,
            "X-Project-Signature-Version": signature_version,
            "X-Timestamp": str(timestamp_ms),
            "X-Nonce": nonce,
            "Idempotency-Key": idempotency_key,
            "X-Signature": signature,
        },
        timestamp_ms=timestamp_ms,
        nonce=nonce,
    )
