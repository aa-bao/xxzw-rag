from __future__ import annotations

import io
import zipfile

import pytest

from src.ingestion.docx import DocxParser
from src.shared.errors import AppError

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _build_docx(paragraph_texts: list[str]) -> bytes:
    """构造一个最小合法 docx 包：每段一个 <w:p><w:r><w:t>。"""
    paragraphs = []
    for text in paragraph_texts:
        paragraphs.append(
            f'<w:p xmlns:w="{W_NS}"><w:r><w:t>{text}</w:t></w:r></w:p>'
        )
    return _build_docx_from_body("".join(paragraphs))


def _build_docx_from_body(body: str) -> bytes:
    document_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}</w:body></w:document>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_docx_parser_extracts_each_paragraph_as_section() -> None:
    data = _build_docx(["第一段：导言", "第二段：正文内容", "第三段：结论"])

    sections = DocxParser().parse(data)

    assert [s.text for s in sections] == ["第一段：导言", "第二段：正文内容", "第三段：结论"]
    assert all(s.page is None for s in sections)


def test_docx_parser_handles_empty_paragraphs() -> None:
    data = _build_docx(["标题", "", "正文"])

    sections = DocxParser().parse(data)

    assert [s.text for s in sections] == ["标题", "", "正文"]


def test_docx_parser_joins_runs_within_paragraph() -> None:
    """同一段落内多个 run（如加粗拆分）应拼接为一个段落文本。"""
    body = (
        f'<w:p><w:r><w:t>加粗</w:t></w:r><w:r><w:t>与</w:t></w:r>'
        f'<w:r><w:t>普通</w:t></w:r></w:p>'
    )
    data = _build_docx_from_body(body)

    sections = DocxParser().parse(data)

    assert [s.text for s in sections] == ["加粗与普通"]


def test_docx_parser_rejects_invalid_zip() -> None:
    with pytest.raises(AppError) as error:
        DocxParser().parse(b"this is not a docx file at all")

    assert error.value.code == "DOCX_INVALID_PACKAGE"


def test_docx_parser_rejects_missing_document_xml() -> None:
    """zip 合法但缺少 word/document.xml（如伪装成 docx 的 zip）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "not a document")
    data = buf.getvalue()

    with pytest.raises(AppError) as error:
        DocxParser().parse(data)

    assert error.value.code == "DOCX_MISSING_DOCUMENT"
