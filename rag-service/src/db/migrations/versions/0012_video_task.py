"""add video task analysis records table

Revision ID: 0012_video_task
Revises: 0011_kb_default_mapping_version
Create Date: 2026-08-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_video_task"
down_revision: Union[str, None] = "0011_kb_default_mapping_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_video_task",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=2000), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("output_dir", sa.String(length=1000), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("frames_requested", sa.Integer(), nullable=True),
        sa.Column("transcript_source", sa.String(length=200), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("keyframes", sa.JSON(), nullable=True),
        sa.Column("cost", sa.JSON(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=True),
        sa.Column("qa_history", sa.JSON(), nullable=True),
        sa.Column("video_path", sa.String(length=1000), nullable=True),
        sa.Column("audio_path", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_video_task_task_id"),
    )
    op.create_index("idx_video_task_created", "rag_video_task", ["created_at"])
    op.create_index("idx_video_task_status", "rag_video_task", ["status"])
    op.create_index("idx_video_task_kind", "rag_video_task", ["kind"])


def downgrade() -> None:
    op.drop_index("idx_video_task_kind", table_name="rag_video_task")
    op.drop_index("idx_video_task_status", table_name="rag_video_task")
    op.drop_index("idx_video_task_created", table_name="rag_video_task")
    op.drop_table("rag_video_task")
