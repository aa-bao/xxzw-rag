from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from src.models.client import ModelError, _classify_error


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

    async def complete(self, messages: list[dict[str, str]]) -> str:
        """Return one non-streaming completion for internal model tasks."""
        url = f"{self._base_url}/chat/completions"
        payload = {"model": self._chat_model, "messages": messages, "stream": False}
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

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        url = f"{self._base_url}/chat/completions"
        payload = {"model": self._chat_model, "messages": messages, "stream": True}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        response = await self._client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            code, retryable = _classify_error(response.status_code)
            raise ModelError(code, response.text or code, retryable=retryable)

        content_emitted = False
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
                    content_emitted = True
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
