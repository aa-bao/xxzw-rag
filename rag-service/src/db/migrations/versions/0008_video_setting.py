"""video agent settings single-row table

Revision ID: 0008_video_setting
Revises: 0007_platform_data_isolation
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008_video_setting'
down_revision: Union[str, None] = '0007_platform_data_isolation'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('video_setting',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('asr_provider', sa.String(length=50), server_default=sa.text("'volcengine'"), nullable=False),
    sa.Column('asr_model', sa.String(length=200), server_default=sa.text("'bigmodel'"), nullable=False),
    sa.Column('asr_api_key', sa.String(length=1000), server_default=sa.text("''"), nullable=False),
    sa.Column('asr_app_id', sa.String(length=200), server_default=sa.text("''"), nullable=False),
    sa.Column('asr_access_token', sa.String(length=1000), server_default=sa.text("''"), nullable=False),
    sa.Column('chat_base_url', sa.String(length=500), server_default=sa.text("''"), nullable=False),
    sa.Column('chat_model', sa.String(length=200), server_default=sa.text("''"), nullable=False),
    sa.Column('chat_api_key', sa.String(length=1000), server_default=sa.text("''"), nullable=False),
    sa.Column('frames', sa.Integer(), server_default=sa.text('12'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('video_setting')
