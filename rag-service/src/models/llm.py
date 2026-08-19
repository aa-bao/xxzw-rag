from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from src.models.client import ModelError, _classify_error

# OpenAI 兼容消息 content：纯文本或多模态 content 数组（图片/文本）。
MessageContent = str | list[dict[str, Any]]


class ChatClient:
    def __init__(self, settings: object, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    @property
    def _base_url(self) -> str:
        return self._settings.model_relay.base_url

    @property
    def _api_key(self) -> str:
        return self._settings.model_relay.api_key.get_secret_value()

    @property
    def _chat_model(self) -> str:
        return self._settings.model_relay.chat_model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
    ) -> str:
        """Return one non-streaming completion for internal model tasks."""
        url = f"{self._base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "messages": messages,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {"Authorization": f"Bearer {self._api_key}"}

        response = await self._client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            code, retryable = _classify_error(response.status_code)
            raise ModelError(code, response.text or code, retryable=retryable)

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("MODEL_INVALID_RESPONSE", "missing completion content") from exc
        return content.strip() if isinstance(content, str) else ""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        url = f"{self._base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "messages": messages,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {"Authorization": f"Bearer {self._api_key}"}

        response = await self._client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            code, retryable = _classify_error(response.status_code)
            raise ModelError(code, response.text or code, retryable=retryable)

        async for line in response.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                event = json.loads(data_str)
                delta = event["choices"][0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
