from __future__ import annotations

import unicodedata


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF  # CJK Extension A
        or 0x4E00 <= codepoint <= 0x9FFF  # CJK Unified Ideographs
        or 0x3040 <= codepoint <= 0x30FF  # Kana
        or 0xF900 <= codepoint <= 0xFAFF  # Compatibility Ideographs
    )


def analyze_lexical(text: str) -> str:
    """Normalize text into a space-joined, deduplicated token string.

    Applies NFKC and lowercasing; keeps alphanumeric identifier runs intact
    (including internal hyphens such as ``SKU-AB12``); emits one unigram and
    one adjacent bigram per character for every contiguous CJK run. Term
    order is stable and each term appears at most once.
    """
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    index = 0
    length = len(normalized)
    while index < length:
        character = normalized[index]
        if _is_cjk(character):
            start = index
            while index < length and _is_cjk(normalized[index]):
                index += 1
            run = normalized[start:index]
            for position, unit in enumerate(run):
                tokens.append(unit)
                if position + 1 < len(run):
                    tokens.append(run[position : position + 2])
            continue
        if character.isalnum():
            start = index
            while index < length and _is_identifier_char(normalized, index):
                index += 1
            tokens.append(normalized[start:index])
            continue
        index += 1
    return " ".join(dict.fromkeys(tokens))


def _is_identifier_char(text: str, index: int) -> bool:
    character = text[index]
    if _is_cjk(character):
        return False  # 数字/字母混合串不吞并后续中文
    if character.isalnum():
        return True
    return character == "-" and index + 1 < len(text) and text[index + 1].isalnum()


def build_match_query(text: str) -> str:
    """Build an FTS5 MATCH expression from analyzed tokens.

    Tokens are joined with ``OR`` so that partial matches still surface and
    BM25 ranks full matches first. Hyphenated identifiers are split back
    into space-separated tokens (``sku-ab12`` becomes ``sku ab12``), because
    the ``unicode61`` tokenizer treats ``-`` as a separator and a bare
    ``sku-ab12`` would be parsed as a NOT expression. No quoting is used, so
    the output never contains double quotes.
    """
    terms: list[str] = []
    for token in analyze_lexical(text).split():
        if "-" in token:
            terms.append(token.replace("-", " "))
        else:
            terms.append(token)
    return " OR ".join(terms)
