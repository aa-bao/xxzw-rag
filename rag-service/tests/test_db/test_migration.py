from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


EXPECTED_TABLES = {
    "rag_user",
    "rag_session",
    "rag_knowledge_base",
    "rag_document",
    "rag_document_job",
    "rag_conversation",
    "rag_conversation_kb",
    "rag_message",
    "rag_reference",
    "rag_query_log",
    "rag_mapping_template",
    "rag_mapping_template_version",
    "rag_document_mapping",
    "rag_ingest_run",
}

EXPECTED_DOCUMENT_COLUMNS = {
    "active_ingest_run_id",
    "processed_path",
    "mapping_errors_path",
}


def _has_composite_fk(
    foreign_keys: list[dict[str, object]],
    constrained_columns: tuple[str, ...],
    referred_table: str,
) -> bool:
    return any(
        tuple(item["constrained_columns"]) == constrained_columns
        and item["referred_table"] == referred_table
        for item in foreign_keys
    )


@pytest.mark.asyncio
async def test_upgrade_head_creates_slice_schema(mysql_url: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = mysql_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    engine = create_async_engine(mysql_url)
    try:
        async with engine.connect() as connection:
            tables, document_columns, document_fks, conversation_kb_fks = await connection.run_sync(
                lambda sync_connection: (
                    set(inspect(sync_connection).get_table_names()),
                    {
                        column["name"]
                        for column in inspect(sync_connection).get_columns("rag_document")
                    },
                    inspect(sync_connection).get_foreign_keys("rag_document"),
                    inspect(sync_connection).get_foreign_keys("rag_conversation_kb"),
                )
            )
    finally:
        await engine.dispose()

    assert EXPECTED_TABLES <= tables
    assert EXPECTED_DOCUMENT_COLUMNS <= document_columns
    assert _has_composite_fk(
        document_fks,
        ("kb_id", "owner_user_id"),
        "rag_knowledge_base",
    )
    # 会话不再持有 kb_id 列，多库绑定走关联表（复合 FK 对齐旧查询级联）
    assert _has_composite_fk(
        conversation_kb_fks,
        ("kb_id", "owner_user_id"),
        "rag_knowledge_base",
    )

