from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from src.shared.errors import AppError


@dataclass(frozen=True)
class Section:
    text: str
    page: int | None


class DocxParser:
    """从 .docx（OOXML 压缩包）中提取正文段落文本。

    docx 本身没有物理分页概念，分页是 Word 渲染时动态计算的，
    因此每个段落作为一个 Section 返回，page 一律为 None。
    """

    def parse(self, data: bytes) -> list[Section]:
        from docx import Document

        try:
            document = Document(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise AppError("DOCX_INVALID_PACKAGE", "DOCX 文件不是有效的压缩包") from None
        except Exception as exc:  # python-docx 对损坏文件抛出多种异常
            raise AppError(
                "DOCX_MISSING_DOCUMENT", "DOCX 文件缺少正文内容（word/document.xml）"
            ) from exc

        return [
            Section(text=paragraph.text, page=None)
            for paragraph in document.paragraphs
        ]
