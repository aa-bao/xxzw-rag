from __future__ import annotations

import pytest

from src.ingestion.factory import ParserFactory
from src.ingestion.text import TxtParser
from src.shared.errors import AppError


def test_txt_parser_is_strict_utf8() -> None:
    factory = ParserFactory()
    factory.register(".txt", "text/plain", TxtParser())

    with pytest.raises(AppError) as error:
        factory.parse("bad.txt", "text/plain", b"\xff")

    assert error.value.code == "TXT_INVALID_ENCODING"


def test_parser_rejects_unknown_type() -> None:
    factory = ParserFactory()

    with pytest.raises(AppError) as error:
        factory.parse("doc.pdf", "application/pdf", b"")

    assert error.value.code == "PARSER_NOT_FOUND"
