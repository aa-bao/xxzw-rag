from __future__ import annotations

import hashlib
import hmac

import pytest

from src.platform.identity import DataScope, PlatformIdentity
from src.platform.signing import sign_request


def test_hmac_signature_uses_frozen_field_order_and_milliseconds() -> None:
    signed = sign_request(
        method="post",
        path_with_query="/api/rpa/tasks?dryRun=true",
        body=b'{"x":1}',
        client_id="client",
        secret="secret",
        secret_version="7",
        idempotency_key="idem",
        timestamp_ms=1_765_000_000_123,
        nonce="nonce",
    )
    canonical = "\n".join(
        [
            "v1",
            "client",
            "POST",
            "/api/rpa/tasks?dryRun=true",
            "1765000000123",
            "nonce",
            "idem",
            hashlib.sha256(b'{"x":1}').hexdigest(),
        ]
    )
    expected = hmac.new(b"secret", canonical.encode(), hashlib.sha256).hexdigest()
    assert signed.headers["X-Signature"] == expected
    assert signed.headers["X-Timestamp"] == "1765000000123"


def test_restricted_empty_scope_fails_closed() -> None:
    scope = DataScope(mode="RESTRICTED", departmentIds=[], userIds=[])
    assert scope.permits("department", "user") is False


def test_snowflake_identifiers_remain_strings() -> None:
    identity = PlatformIdentity.model_validate(
        {
            "tenantId": "000000",
            "userId": "2068630213954715649",
            "username": "operator",
            "displayName": "Operator",
            "departmentId": "2068630213954715650",
            "departmentName": "服装",
            "departmentCategory": "",
            "roles": ["operator"],
            "permissions": ["rag-database:project:view"],
            "dataScope": {"mode": "ALL", "departmentIds": [], "userIds": []},
            "issuedAt": "2026-08-12 10:00:00",
        }
    )
    assert identity.userId == "2068630213954715649"
    with pytest.raises(Exception):
        PlatformIdentity.model_validate({**identity.model_dump(), "userId": 123})
