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
    "rag_message",
    "rag_reference",
    "rag_query_log",
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
            tables, document_fks, conversation_fks = await connection.run_sync(
                lambda sync_connection: (
                    set(inspect(sync_connection).get_table_names()),
                    inspect(sync_connection).get_foreign_keys("rag_document"),
                    inspect(sync_connection).get_foreign_keys("rag_conversation"),
                )
            )
    finally:
        await engine.dispose()

    assert EXPECTED_TABLES <= tables
    assert _has_composite_fk(
        document_fks,
        ("kb_id", "owner_user_id"),
        "rag_knowledge_base",
    )
    assert _has_composite_fk(
        conversation_fks,
        ("kb_id", "owner_user_id"),
        "rag_knowledge_base",
    )

