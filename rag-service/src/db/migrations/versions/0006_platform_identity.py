"""platform identity and project sessions

Revision ID: 0006_platform_identity
Revises: 0005_structured_json_ingestion
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_platform_identity"
down_revision: Union[str, None] = "0005_structured_json_ingestion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rag_user", sa.Column("platform_tenant_id", sa.String(64), nullable=True))
    op.add_column("rag_user", sa.Column("platform_user_id", sa.String(64), nullable=True))
    op.add_column("rag_user", sa.Column("platform_department_id", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uk_rag_user_platform_identity",
        "rag_user",
        ["platform_tenant_id", "platform_user_id"],
    )
    op.create_table(
        "rag_platform_session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("identity_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["rag_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uk_platform_session_token"),
    )
    op.create_index("idx_platform_session_expiry", "rag_platform_session", ["expires_at"])
    op.create_index("idx_platform_session_user", "rag_platform_session", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_table("rag_platform_session")
    op.drop_constraint("uk_rag_user_platform_identity", "rag_user", type_="unique")
    op.drop_column("rag_user", "platform_department_id")
    op.drop_column("rag_user", "platform_user_id")
    op.drop_column("rag_user", "platform_tenant_id")
