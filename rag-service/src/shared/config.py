from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr


_ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AppSettings(FrozenModel):
    browser_origin: str
    secure_cookie: bool = False


class RagSettings(FrozenModel):
    chroma_mode: Literal["persist", "http"] = "persist"
    chroma_persist_dir: Path
    default_chunk_size: int = Field(ge=64, le=4096)
    default_overlap: int = Field(ge=0)
    top_k: int = Field(ge=1)
    similarity_threshold: float = Field(ge=0, le=1)


class ModelRelaySettings(FrozenModel):
    base_url: str
    api_key: SecretStr
    embedding_model: str
    chat_model: str
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    timeout_seconds: float = Field(gt=0)
    embedding_max_retries: int = Field(ge=1)
    chat_pre_stream_max_retries: int = Field(ge=0)
    retry_base_delay_seconds: float = Field(gt=0)

    def model_post_init(self, _context: object) -> None:
        if not self.embedding_base_url:
            object.__setattr__(self, "embedding_base_url", self.base_url)
        if self.embedding_api_key.get_secret_value() == "":
            object.__setattr__(self, "embedding_api_key", self.api_key)


class DatabaseSettings(FrozenModel):
    url: SecretStr
    pool_size: int = Field(ge=1)
    pool_recycle_seconds: int = Field(ge=1)


class UploadSettings(FrozenModel):
    root_dir: Path
    temp_dir: Path
    max_size_mb: int = Field(ge=1, le=100)
    allowed_extensions: tuple[str, ...]


class LlmSettings(FrozenModel):
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(ge=1)
    max_history_tokens: int = Field(ge=1)
    system_prompt: str = Field(min_length=1)


class Settings(FrozenModel):
    app: AppSettings
    rag: RagSettings
    model_relay: ModelRelaySettings
    database: DatabaseSettings
    upload: UploadSettings
    llm: LlmSettings
    empty_response: str = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> "Settings":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Configuration root must be a mapping: {path}")
        return cls.model_validate(_expand_environment(raw))


def _expand_environment(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if not isinstance(value, str):
        return value

    match = _ENV_PLACEHOLDER.fullmatch(value)
    if match is None:
        return value

    name = match.group(1)
    resolved = os.environ.get(name)
    if resolved is None:
        raise ValueError(f"Missing required environment variable: {name}")
    return resolved

