"""add video qa_base_url and qa_api_key

Revision ID: 0010_video_qa_credentials
Revises: 0009_video_qa_model
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0010_video_qa_credentials'
down_revision: Union[str, None] = '0009_video_qa_model'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'video_setting',
        sa.Column('qa_base_url', sa.String(length=500), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        'video_setting',
        sa.Column('qa_api_key', sa.String(length=1000), server_default=sa.text("''"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('video_setting', 'qa_api_key')
    op.drop_column('video_setting', 'qa_base_url')
