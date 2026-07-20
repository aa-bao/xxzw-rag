from importlib import import_module
from pathlib import Path

import pytest


def _load_settings_class():
    try:
        module = import_module("src.shared.config")
    except ModuleNotFoundError:
        return None
    return getattr(module, "Settings", None)


def _write_config(path: Path) -> None:
    path.write_text(
        """
app:
  browser_origin: http://127.0.0.1:8000
  secure_cookie: false
rag:
  chroma_mode: persist
  chroma_persist_dir: ./data/chroma
  default_chunk_size: 512
  default_overlap: 50
  top_k: 5
  similarity_threshold: 0.2
model_relay:
  base_url: ${MODEL_RELAY_BASE_URL}
  api_key: ${MODEL_RELAY_API_KEY}
  embedding_model: ${EMBEDDING_MODEL}
  chat_model: ${CHAT_MODEL}
  timeout_seconds: 60
  embedding_max_retries: 3
  chat_pre_stream_max_retries: 1
  retry_base_delay_seconds: 1
database:
  url: ${DATABASE_URL}
  pool_size: 10
  pool_recycle_seconds: 1800
upload:
  root_dir: ./data/uploads
  temp_dir: ./data/tmp
  max_size_mb: 100
  allowed_extensions: [txt]
llm:
  temperature: 0.7
  max_tokens: 4096
  max_history_tokens: 4096
  system_prompt: system
empty_response: empty
""".strip(),
        encoding="utf-8",
    )


def test_secret_must_come_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings_class = _load_settings_class()
    assert settings_class is not None, "Settings has not been implemented"

    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    monkeypatch.setenv("MODEL_RELAY_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.delenv("MODEL_RELAY_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "embed")
    monkeypatch.setenv("CHAT_MODEL", "chat")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://rag:test@127.0.0.1/rag")

    with pytest.raises(ValueError, match="MODEL_RELAY_API_KEY"):
        settings_class.load(config_path)

