from __future__ import annotations

import asyncio
from typing import Any

import httpx


class ModelError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, content_emitted: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.content_emitted = content_emitted


def _classify_error(status: int) -> tuple[str, bool]:
    if status == 401 or status == 403:
        return "MODEL_AUTH_FAILED", False
    if status == 404:
        return "MODEL_NOT_FOUND", False
    if status == 429:
        return "MODEL_RATE_LIMITED", True
    if status >= 500:
        return "MODEL_UPSTREAM_ERROR", True
    if status == 422:
        return "MODEL_INVALID_REQUEST", False
    return "MODEL_UNEXPECTED", False


class ModelRelayClient:
    def __init__(self, settings: Any, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    @property
    def _base_url(self) -> str:
        return self._settings.model_relay.base_url

    @property
    def _api_key(self) -> str:
        return self._settings.model_relay.api_key.get_secret_value()

    @property
    def _embedding_model(self) -> str:
        return self._settings.model_relay.embedding_model

    @property
    def _embedding_base_url(self) -> str:
        return self._settings.model_relay.embedding_base_url or self._base_url

    @property
    def _embedding_api_key(self) -> str:
        ek = self._settings.model_relay.embedding_api_key.get_secret_value()
        return ek if ek else self._api_key

    @property
    def _embedding_max_retries(self) -> int:
        return self._settings.model_relay.embedding_max_retries

    @property
    def _retry_delay(self) -> float:
        return self._settings.model_relay.retry_base_delay_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 豆包多模态向量模型：专用 /embeddings/multimodal 端点，单条输入、dict 响应，
        # 批量由并发逐条调用实现（该模型不支持请求内批量）。
        if self._embedding_model.startswith("doubao-embedding-vision"):
            return await self._embed_multimodal(texts)

        url = f"{self._embedding_base_url}/embeddings"
        payload = {"model": self._embedding_model, "input": texts}
        headers = {"Authorization": f"Bearer {self._embedding_api_key}"}

        last_error: ModelError | None = None
        for attempt in range(self._embedding_max_retries):
            response = await self._client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return [item["embedding"] for item in data["data"]]

            code, retryable = _classify_error(response.status_code)
            last_error = ModelError(code, response.text or code, retryable=retryable)
            if not retryable:
                raise last_error

            if attempt < self._embedding_max_retries - 1:
                await asyncio.sleep(self._retry_delay * (2 ** attempt))

        raise last_error or ModelError("MODEL_UNEXPECTED", "unexpected")

    async def _embed_multimodal(self, texts: list[str]) -> list[list[float]]:
        url = f"{self._embedding_base_url}/embeddings/multimodal"
        headers = {"Authorization": f"Bearer {self._embedding_api_key}"}

        async def embed_one(text: str) -> list[float]:
            payload = {
                "model": self._embedding_model,
                "input": [{"type": "text", "text": text}],
                # 固定 1024 维：与历史 Chroma 集合维度一致，避免换模型后库重建
                "dimensions": 1024,
            }
            last_error: ModelError | None = None
            for attempt in range(self._embedding_max_retries):
                response = await self._client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    return response.json()["data"]["embedding"]

                code, retryable = _classify_error(response.status_code)
                last_error = ModelError(code, response.text or code, retryable=retryable)
                if not retryable:
                    raise last_error

                if attempt < self._embedding_max_retries - 1:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))

            raise last_error or ModelError("MODEL_UNEXPECTED", "unexpected")

        return list(await asyncio.gather(*(embed_one(text) for text in texts)))

    async def probe_dimension(self) -> int:
        vectors = await self.embed(["dimension probe"])
        return len(vectors[0])
