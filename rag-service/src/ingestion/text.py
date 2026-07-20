from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    text: str
    page: int | None


class TxtParser:
    def parse(self, data: bytes) -> list[Section]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            from src.shared.errors import AppError

            raise AppError("TXT_INVALID_ENCODING", "TXT 文件必须为 UTF-8 编码")

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return [Section(text=text, page=None)]
