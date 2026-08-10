from __future__ import annotations

import unicodedata


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(character for character in normalized if character.isalnum())


def lexical_score(query: str, content: str) -> float:
    normalized_query = _normalize(query)
    normalized_content = _normalize(content)
    if not normalized_query or not normalized_content:
        return 0.0
    if normalized_query in normalized_content:
        return 1.0
    if len(normalized_query) == 1 or len(normalized_content) == 1:
        return float(normalized_query == normalized_content)

    query_bigrams = {
        normalized_query[index : index + 2]
        for index in range(len(normalized_query) - 1)
    }
    content_bigrams = {
        normalized_content[index : index + 2]
        for index in range(len(normalized_content) - 1)
    }
    return 2.0 * len(query_bigrams & content_bigrams) / (
        len(query_bigrams) + len(content_bigrams)
    )


def hybrid_score(cosine: float, lexical: float) -> float:
    return 0.75 * cosine + 0.25 * lexical
