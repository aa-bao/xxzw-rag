from __future__ import annotations

import json
from collections import deque
from typing import Any

import httpx
import pytest

from src.models.client import ModelRelayClient
from src.models.llm import ChatClient, ModelError


class _FakeStreamTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self._queue: deque[bytes] = deque()
        self.request_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        if not self._queue:
            return httpx.Response(503, json={}, request=request)
        body = self._queue.popleft()
        stream = httpx.ByteStream(body)
        return httpx.Response(200, stream=stream, request=request)

    def enqueue_status(self, status: int) -> None:
        self._queue.append(b"")

    def enqueue_sse(self, *contents: str) -> None:
        lines = []
        for c in contents:
            lines.append(
                f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n".encode()
            )
        lines.append(b"data: [DONE]\n\n")
        self._queue.append(b"".join(lines))


@pytest.fixture
def sse_transport() -> _FakeStreamTransport:
    return _FakeStreamTransport()


@pytest.fixture
def chat_client(sse_transport: _FakeStreamTransport) -> ChatClient:
    settings = type(
        "S",
        (),
        {
            "model_relay": type(
                "R",
                (),
                {
                    "base_url": "http://127.0.0.1:9000/v1",
                    "api_key": type("K", (), {"get_secret_value": lambda s: "k"})(),
                    "chat_model": "test-chat",
                },
            )(),
        },
    )()
    httpx_client = httpx.AsyncClient(transport=sse_transport)
    return ChatClient(settings, httpx_client)


async def test_chat_yields_openai_compatible_deltas(
    chat_client: ChatClient, sse_transport: _FakeStreamTransport
) -> None:
    sse_transport.enqueue_sse("hello", " world")

    parts = [part async for part in chat_client.stream([{"role": "user", "content": "x"}])]

    assert parts == ["hello", " world"]


async def test_chat_exposes_whether_failure_happened_before_content(
    chat_client: ChatClient, sse_transport: _FakeStreamTransport
) -> None:
    # For a 503, the client raises before any content is emitted
    with pytest.raises(ModelError) as error:
        _ = [part async for part in chat_client.stream([{"role": "user", "content": "x"}])]

    assert error.value.retryable
    assert not error.value.content_emitted
