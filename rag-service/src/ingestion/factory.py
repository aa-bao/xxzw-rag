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
        # 优先精确匹配 (扩展名, media_type)；其次按扩展名兜底（不依赖客户端 content-type）
        parser = _PARSERS.get((ext, media_type.lower()))
        if parser is None:
            parser = _PARSERS.get((ext, "*"))
        if parser is None:
            # 扩展名已注册但 media_type 不匹配：按扩展名解析
            for (registered_ext, _mime), candidate in _PARSERS.items():
                if registered_ext == ext:
                    parser = candidate
                    break
        if parser is None:
            raise AppError("PARSER_NOT_FOUND", f"不支持的文件类型: {ext}")
        result = parser.parse(data)
        return [Section(text=s.text, page=s.page) for s in result]
