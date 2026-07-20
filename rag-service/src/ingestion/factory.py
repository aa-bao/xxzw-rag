from __future__ import annotations

from dataclasses import dataclass

from src.ingestion.text import TxtParser
from src.shared.errors import AppError

_PARSERS: dict[tuple[str, str], object] = {}


@dataclass
class Section:
    text: str
    page: int | None


class ParserFactory:
    def register(self, extension: str, media_type: str, parser: object) -> None:
        _PARSERS[(extension.lower(), media_type.lower())] = parser

    def parse(self, filename: str, media_type: str, data: bytes) -> list[Section]:
        import os

        ext = os.path.splitext(filename)[1].lower()
        key = (ext, media_type.lower())
        parser = _PARSERS.get(key)
        if parser is None:
            raise AppError("PARSER_NOT_FOUND", f"不支持的文件类型: {ext}")
        result = parser.parse(data)
        return [Section(text=s.text, page=s.page) for s in result]
