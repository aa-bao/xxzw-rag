"""kb cover image path

Revision ID: 0003_kb_cover
Revises: 0002_model_setting
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003_kb_cover'
down_revision: Union[str, None] = '0002_model_setting'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'rag_knowledge_base',
        sa.Column('cover_path', sa.String(length=1000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('rag_knowledge_base', 'cover_path')
