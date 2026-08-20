"""add knowledge base default mapping version

Revision ID: 0011_kb_default_mapping_version
Revises: 0010_video_qa_credentials
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_kb_default_mapping_version"
down_revision: Union[str, None] = "0010_video_qa_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rag_knowledge_base",
        sa.Column("default_mapping_version_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_kb_default_mapping_version",
        "rag_knowledge_base",
        "rag_mapping_template_version",
        ["default_mapping_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_kb_default_mapping_version", "rag_knowledge_base", type_="foreignkey")
    op.drop_column("rag_knowledge_base", "default_mapping_version_id")
