from __future__ import annotations

import hashlib
from typing import Any


def split_text(
    doc_id: int,
    text: str,
    *,
    chunk_size: int = 512,
    overlap: int = 50,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_text = text[start:end]
        content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
        chunk_id = f"{doc_id}:{len(chunks)}:{content_hash[:16]}"
        chunks.append({"chunk_id": chunk_id, "content": chunk_text, "index": len(chunks)})
        if end >= len(text):
            break
        start = end - overlap
    return chunks
