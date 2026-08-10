from __future__ import annotations

import json
import time
from collections import deque
from typing import Any

import httpx
import pytest

from src.models.client import ModelRelayClient, ModelError


class FakeTransport(httpx.AsyncBaseTransport):
    """Queue-based fake transport — enqueue (status_code, headers, body) tuples."""

    def __init__(self) -> None:
        self._queue: deque[tuple[int, dict[str, str], dict[str, Any] | list[dict[str, Any]]]] = deque()
        self.request_count = 0
        self.requests: list[httpx.Request] = []
        self.last_json: dict[str, Any] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        self.requests.append(request)
        if request.content:
            self.last_json = json.loads(request.content)
        if not self._queue:
            return httpx.Response(500, json={})
        status, headers, body = self._queue.popleft()
        json_content: dict[str, Any] | list[dict[str, Any]]
        if isinstance(body, list):
            json_content = body
        else:
            json_content = body
        return httpx.Response(status, json=json_content, headers=headers, request=request)

    def enqueue(self, *items: int | dict[str, Any]) -> None:
        """Enqueue one or more items. int → status with empty body; dict → 200 with that body."""
        for item in items:
            if isinstance(item, int):
                self._queue.append((item, {}, {}))
            else:
                self._queue.append((200, {"content-type": "application/json"}, item))


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport: FakeTransport) -> ModelRelayClient:
    settings = type(
        "Settings",
        (),
        {
            "model_relay": type(
                "R",
                (),
                {
                    "base_url": "http://127.0.0.1:9000/v1",
                    "api_key": type("K", (), {"get_secret_value": lambda s: "test-key"})(),
                    "embedding_model": "test-embedding",
                    "chat_model": "test-chat",
                    "timeout_seconds": 60.0,
                    "embedding_max_retries": 3,
                    "chat_pre_stream_max_retries": 1,
                    "retry_base_delay_seconds": 0.01,
                    "embedding_base_url": "",
                    "embedding_api_key": type("K", (), {"get_secret_value": lambda s: ""})(),
                },
            )(),
        },
    )()
    httpx_client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000/v1")
    return ModelRelayClient(settings, httpx_client)  # type: ignore[arg-type]


async def test_embedding_retries_retryable_errors_three_total_attempts(
    client: ModelRelayClient, transport: FakeTransport
) -> None:
    transport.enqueue(503, 503, {"data": [{"embedding": [0.1, 0.2]}]})

    result = await client.embed(["x"])

    assert result == [[0.1, 0.2]]
    assert transport.request_count == 3


async def test_bad_api_key_is_not_retried(
    client: ModelRelayClient, transport: FakeTransport
) -> None:
    transport.enqueue(401)

    with pytest.raises(ModelError) as error:
        await client.embed(["x"])

    assert error.value.code == "MODEL_AUTH_FAILED"
    assert transport.request_count == 1


async def test_probe_dimension_uses_configured_model(
    client: ModelRelayClient, transport: FakeTransport
) -> None:
    transport.enqueue({"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    dim = await client.probe_dimension()

    assert dim == 3
    assert transport.last_json.get("model") == "test-embedding"


def _multimodal_client(transport: FakeTransport) -> ModelRelayClient:
    settings = type(
        "Settings",
        (),
        {
            "model_relay": type(
                "R",
                (),
                {
                    "base_url": "http://127.0.0.1:9000/v1",
                    "api_key": type("K", (), {"get_secret_value": lambda s: "test-key"})(),
                    "embedding_model": "doubao-embedding-vision-251215",
                    "chat_model": "test-chat",
                    "timeout_seconds": 60.0,
                    "embedding_max_retries": 2,
                    "chat_pre_stream_max_retries": 1,
                    "retry_base_delay_seconds": 0.01,
                    "embedding_base_url": "",
                    "embedding_api_key": type("K", (), {"get_secret_value": lambda s: ""})(),
                },
            )(),
        },
    )()
    httpx_client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000/v1")
    return ModelRelayClient(settings, httpx_client)  # type: ignore[arg-type]


async def test_embedding_multimodal_uses_vision_endpoint_and_dict_response(
    transport: FakeTransport,
) -> None:
    client = _multimodal_client(transport)
    transport.enqueue({"data": {"embedding": [0.1, 0.2, 0.3]}})

    result = await client.embed(["x"])

    assert result == [[0.1, 0.2, 0.3]]
    assert transport.request_count == 1
    request = transport.requests[0]
    assert request.url.path == "/v1/embeddings/multimodal"
    assert transport.last_json == {
        "model": "doubao-embedding-vision-251215",
        "input": [{"type": "text", "text": "x"}],
        "dimensions": 1024,
    }


async def test_embedding_multimodal_batch_calls_concurrently(
    transport: FakeTransport,
) -> None:
    client = _multimodal_client(transport)
    transport.enqueue(
        {"data": {"embedding": [1.0]}},
        {"data": {"embedding": [2.0]}},
    )

    result = await client.embed(["a", "b"])

    assert result == [[1.0], [2.0]]
    assert transport.request_count == 2


async def test_embedding_multimodal_retries_then_raises(
    transport: FakeTransport,
) -> None:
    client = _multimodal_client(transport)
    transport.enqueue(503, 503)

    with pytest.raises(ModelError) as error:
        await client.embed(["x"])

    assert error.value.code == "MODEL_UPSTREAM_ERROR"
    assert transport.request_count == 2
