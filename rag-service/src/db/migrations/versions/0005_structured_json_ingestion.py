"""structured JSON ingestion persistence

Revision ID: 0005_structured_json_ingestion
Revises: 0004_conversation_multi_kb
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_structured_json_ingestion"
down_revision: Union[str, None] = "0004_conversation_multi_kb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOCUMENT_STATUSES = (
    "'pending','running','uploaded','profiling','awaiting_mapping','previewing',"
    "'queued','mapping','chunking','embedding','indexing_lexical','indexing_dense',"
    "'activating','done','done_with_warnings','failed','deleting'"
)

DOCUMENT_JOB_STAGES = (
    "'parsing','indexing','profiling','awaiting_mapping','previewing','queued',"
    "'mapping','chunking','embedding','indexing_lexical','indexing_dense','activating'"
)


def upgrade() -> None:
    op.create_table(
        "rag_mapping_template",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_format", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["rag_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "rag_mapping_template_version",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mapping_template_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mapping_json", sa.Text(), nullable=False),
        sa.Column("structure_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["rag_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mapping_template_id"], ["rag_mapping_template.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mapping_template_id", "version", name="uk_mapping_template_version"),
    )
    op.create_table(
        "rag_document_mapping",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("mapping_version_id", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["rag_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["rag_document.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mapping_version_id"], ["rag_mapping_template_version.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uk_document_mapping_document"),
    )
    op.create_table(
        "rag_ingest_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("total_records", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("processed_records", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_path", sa.String(length=1000), nullable=True),
        sa.Column("mapping_errors_path", sa.String(length=1000), nullable=True),
        sa.Column("manifest_path", sa.String(length=1000), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("chunk_count >= 0", name="ck_ingest_run_chunk_count"),
        sa.CheckConstraint("error_count >= 0", name="ck_ingest_run_error_count"),
        sa.CheckConstraint("processed_records >= 0", name="ck_ingest_run_processed_records"),
        sa.CheckConstraint("total_records >= 0", name="ck_ingest_run_total_records"),
        sa.ForeignKeyConstraint(["document_id"], ["rag_document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ingest_run_document", "rag_ingest_run", ["document_id", "created_at"])

    op.add_column("rag_document", sa.Column("active_ingest_run_id", sa.String(length=36), nullable=True))
    op.add_column("rag_document", sa.Column("processed_path", sa.String(length=1000), nullable=True))
    op.add_column("rag_document", sa.Column("mapping_errors_path", sa.String(length=1000), nullable=True))
    op.create_foreign_key(
        "fk_document_active_ingest_run",
        "rag_document",
        "rag_ingest_run",
        ["active_ingest_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("ck_rag_document_status", "rag_document", type_="check")
    op.create_check_constraint(
        "ck_rag_document_status", "rag_document", f"status IN ({DOCUMENT_STATUSES})"
    )
    op.drop_constraint("ck_rag_document_job_stage", "rag_document_job", type_="check")
    op.create_check_constraint(
        "ck_rag_document_job_stage",
        "rag_document_job",
        f"stage IS NULL OR stage IN ({DOCUMENT_JOB_STAGES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rag_document_job_stage", "rag_document_job", type_="check")
    op.create_check_constraint(
        "ck_rag_document_job_stage",
        "rag_document_job",
        "stage IS NULL OR stage IN ('parsing','chunking','embedding','indexing')",
    )
    op.drop_constraint("ck_rag_document_status", "rag_document", type_="check")
    op.create_check_constraint(
        "ck_rag_document_status",
        "rag_document",
        "status IN ('pending','running','done','failed','deleting')",
    )

    op.drop_constraint("fk_document_active_ingest_run", "rag_document", type_="foreignkey")
    op.drop_column("rag_document", "mapping_errors_path")
    op.drop_column("rag_document", "processed_path")
    op.drop_column("rag_document", "active_ingest_run_id")
    op.drop_index("idx_ingest_run_document", table_name="rag_ingest_run")
    op.drop_table("rag_ingest_run")
    op.drop_table("rag_document_mapping")
    op.drop_table("rag_mapping_template_version")
    op.drop_table("rag_mapping_template")
