"""conversation multi-kb binding

会话由单知识库（rag_conversation.kb_id）改为多知识库关联表
rag_conversation_kb；rag_reference 追加 kb_id/kb_name 来源快照列。

Revision ID: 0004_conversation_multi_kb
Revises: 0003_kb_cover
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004_conversation_multi_kb'
down_revision: Union[str, None] = '0003_kb_cover'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 建会话-知识库关联表（对齐 ConversationKb 模型定义）
    op.create_table('rag_conversation_kb',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('kb_id', sa.BigInteger(), nullable=False),
    sa.Column('owner_user_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['rag_conversation.id'], name='fk_conversation_kb_conversation', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['kb_id', 'owner_user_id'], ['rag_knowledge_base.id', 'rag_knowledge_base.owner_user_id'], name='fk_conversation_kb_kb_owner', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', 'kb_id', name='uk_conversation_kb')
    )
    op.create_index('idx_conversation_kb_kb', 'rag_conversation_kb', ['kb_id', 'owner_user_id'], unique=False)

    # 2. rag_reference 加 kb_id/kb_name 来源快照列（可空，无 FK）
    op.add_column('rag_reference', sa.Column('kb_id', sa.BigInteger(), nullable=True))
    op.add_column('rag_reference', sa.Column('kb_name', sa.String(length=500), nullable=True))

    # 3. 回填（必须在 drop 旧列之前，否则历史数据丢失）
    #    a. 会话的 kb_id 逐一写入关联表
    op.execute(
        "INSERT INTO rag_conversation_kb (conversation_id, kb_id, owner_user_id) "
        "SELECT id, kb_id, owner_user_id FROM rag_conversation"
    )
    #    b. 历史引用按会话所属知识库回填来源快照
    op.execute(
        "UPDATE rag_reference r "
        "JOIN rag_message m ON r.message_id = m.id "
        "JOIN rag_conversation c ON m.conversation_id = c.id "
        "SET r.kb_id = c.kb_id, "
        "r.kb_name = (SELECT name FROM rag_knowledge_base WHERE id = c.kb_id) "
        "WHERE r.kb_id IS NULL"
    )

    # 4. drop 会话上的 kb_id 列与其复合外键
    op.drop_constraint('fk_conversation_kb_owner', 'rag_conversation', type_='foreignkey')
    op.drop_column('rag_conversation', 'kb_id')


def downgrade() -> None:
    # 有损回滚：多库会话无法完整还原，先删除不再绑定任何知识库的孤儿会话
    # （MySQL 不允许 UPDATE/DELETE 中直接 SELECT 同一表关联引用，用 EXISTS 子查询规避）
    op.execute(
        "DELETE FROM rag_conversation WHERE NOT EXISTS ("
        "SELECT 1 FROM rag_conversation_kb "
        "WHERE rag_conversation_kb.conversation_id = rag_conversation.id)"
    )
    # 加回 kb_id 列（先可空），取关联表第一条回填
    op.add_column('rag_conversation', sa.Column('kb_id', sa.BigInteger(), nullable=True))
    op.execute(
        "UPDATE rag_conversation c SET c.kb_id = ("
        "SELECT kb_id FROM rag_conversation_kb k "
        "WHERE k.conversation_id = c.id LIMIT 1)"
    )
    op.alter_column(
        'rag_conversation', 'kb_id', existing_type=sa.BigInteger(), nullable=False
    )
    op.create_foreign_key(
        'fk_conversation_kb_owner',
        'rag_conversation',
        'rag_knowledge_base',
        ['kb_id', 'owner_user_id'],
        ['id', 'owner_user_id'],
        ondelete='CASCADE',
    )
    # 先 drop 关联表上的两个 FK，再删索引与表（MySQL 要求 FK 依赖的索引在删表前移除）
    op.drop_constraint('fk_conversation_kb_conversation', 'rag_conversation_kb', type_='foreignkey')
    op.drop_constraint('fk_conversation_kb_kb_owner', 'rag_conversation_kb', type_='foreignkey')
    op.drop_index('idx_conversation_kb_kb', table_name='rag_conversation_kb')
    op.drop_table('rag_conversation_kb')
    op.drop_column('rag_reference', 'kb_name')
    op.drop_column('rag_reference', 'kb_id')
