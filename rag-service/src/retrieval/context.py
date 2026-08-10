from __future__ import annotations

import re

from src.retrieval.module import RetrievedChunk

try:  # pragma: no cover - availability depends on deployment extras
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _ENCODER = None


def _token_count(text: str) -> int:
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    rest = len(text) - cjk
    return cjk * 2 + rest // 4 + 1


def pack_context(
    chunks: list[RetrievedChunk],
    *,
    max_chunks: int = 8,
    max_tokens: int = 2000,
) -> list[RetrievedChunk]:
    """Pack unique non-empty chunks in input order under fixed context limits."""
    if max_chunks <= 0:
        return []

    packed: list[RetrievedChunk] = []
    seen: set[str] = set()
    tokens = 0
    for chunk in chunks:
        if chunk.chunk_id in seen or not chunk.content.strip():
            continue
        chunk_tokens = _token_count(chunk.content)
        if packed and (len(packed) >= max_chunks or tokens + chunk_tokens > max_tokens):
            break
        packed.append(chunk)
        seen.add(chunk.chunk_id)
        tokens += chunk_tokens
    return packed
