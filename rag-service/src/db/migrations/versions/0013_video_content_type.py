"""add video task content type and image-text post fields

Revision ID: 0013_video_content_type
Revises: 0012_video_task
Create Date: 2026-08-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_video_content_type"
down_revision: Union[str, None] = "0012_video_task"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rag_video_task",
        sa.Column("content_type", sa.String(length=20), server_default=sa.text("'video'"), nullable=False),
    )
    op.add_column(
        "rag_video_task",
        sa.Column("post_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "rag_video_task",
        sa.Column("author", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "rag_video_task",
        sa.Column("hashtags", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rag_video_task",
        sa.Column("publish_time", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "rag_video_task",
        sa.Column("post_images", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rag_video_task",
        sa.Column("image_captions", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rag_video_task", "image_captions")
    op.drop_column("rag_video_task", "post_images")
    op.drop_column("rag_video_task", "publish_time")
    op.drop_column("rag_video_task", "hashtags")
    op.drop_column("rag_video_task", "author")
    op.drop_column("rag_video_task", "post_text")
    op.drop_column("rag_video_task", "content_type")
