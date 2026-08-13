"""platform data isolation columns (tenant/department ownership)

Expand/Contract: 新增 nullable 归属列（不破坏现有关系与既有数据）；
LOCAL 模式下为空，平台模式下由服务端写入。

Revision ID: 0007_platform_data_isolation
Revises: 0006_platform_identity
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_platform_data_isolation"
down_revision: Union[str, None] = "0006_platform_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = [
    "rag_knowledge_base",
    "rag_document",
    "rag_document_job",
    "rag_mapping_template",
    "rag_ingest_run",
    "rag_conversation",
    "rag_conversation_kb",
    "rag_message",
    "rag_query_log",
]


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("department_id", sa.String(64), nullable=True))
        op.create_index(f"idx_{table}_tenant", table, ["tenant_id"])


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"idx_{table}_tenant", table_name=table)
        op.drop_column(table, "department_id")
        op.drop_column(table, "tenant_id")
