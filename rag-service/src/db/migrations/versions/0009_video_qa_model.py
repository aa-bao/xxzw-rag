"""add video qa_model

Revision ID: 0009_video_qa_model
Revises: 0008_video_setting
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009_video_qa_model'
down_revision: Union[str, None] = '0008_video_setting'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'video_setting',
        sa.Column('qa_model', sa.String(length=200), server_default=sa.text("''"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('video_setting', 'qa_model')
