"""model settings single-row table

Revision ID: 0002_model_setting
Revises: 0001_vertical_slice
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002_model_setting'
down_revision: Union[str, None] = '0001_vertical_slice'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('rag_model_setting',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('base_url', sa.String(length=500), nullable=False),
    sa.Column('api_key', sa.String(length=1000), nullable=False),
    sa.Column('chat_model', sa.String(length=200), nullable=False),
    sa.Column('embedding_model', sa.String(length=200), nullable=False),
    sa.Column('embedding_base_url', sa.String(length=500), nullable=True),
    sa.Column('embedding_api_key', sa.String(length=1000), nullable=True),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('rag_model_setting')
