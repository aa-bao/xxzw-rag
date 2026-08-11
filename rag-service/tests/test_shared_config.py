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


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_RELAY_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("MODEL_RELAY_API_KEY", "secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed")
    monkeypatch.setenv("CHAT_MODEL", "chat")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://rag:test@127.0.0.1/rag")


LEXICAL_SECTION = (
    "\nlexical:\n"
    "  sqlite_path: ./data/fts/retrieval.db\n"
    "  dense_top_k: 50\n"
    "  lexical_top_k: 50\n"
    "  rrf_rank_constant: 60\n"
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


def test_lexical_settings_load_with_fixed_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_class = _load_settings_class()
    assert settings_class is not None, "Settings has not been implemented"

    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write(LEXICAL_SECTION)
    _set_required_env(monkeypatch)

    settings = settings_class.load(config_path)
    assert settings.lexical is not None
    assert settings.lexical.sqlite_path == Path("./data/fts/retrieval.db")
    assert settings.lexical.dense_top_k == 50
    assert settings.lexical.lexical_top_k == 50
    assert settings.lexical.rrf_rank_constant == 60


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("dense_top_k", 0), ("dense_top_k", -5), ("lexical_top_k", 0)],
)
def test_lexical_rejects_nonpositive_lane_sizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    bad_value: int,
) -> None:
    settings_class = _load_settings_class()
    assert settings_class is not None, "Settings has not been implemented"

    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\nlexical:\n  sqlite_path: ./data/fts/retrieval.db\n  {field}: {bad_value}\n")
    _set_required_env(monkeypatch)

    with pytest.raises(Exception) as excinfo:
        settings_class.load(config_path)
    assert field in str(excinfo.value)


def test_lexical_rejects_rrf_rank_constant_below_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_class = _load_settings_class()
    assert settings_class is not None, "Settings has not been implemented"

    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write("\nlexical:\n  sqlite_path: ./data/fts/retrieval.db\n  rrf_rank_constant: 0\n")
    _set_required_env(monkeypatch)

    with pytest.raises(Exception) as excinfo:
        settings_class.load(config_path)
    assert "rrf_rank_constant" in str(excinfo.value)


def test_lexical_is_optional_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_class = _load_settings_class()
    assert settings_class is not None, "Settings has not been implemented"

    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _set_required_env(monkeypatch)

    settings = settings_class.load(config_path)
    assert settings.lexical is None
