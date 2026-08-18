from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


CURRENT_TIMESTAMP = text("CURRENT_TIMESTAMP")


class ModelSetting(Base):
    """模型配置持久化，单行（id=1）。api_key 明文存储（内部系统可接受），响应中绝不返回。"""

    __tablename__ = "rag_model_setting"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    chat_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    # null 表示与 chat 共用
    embedding_base_url: Mapped[str | None] = mapped_column(String(500))
    embedding_api_key: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=CURRENT_TIMESTAMP,
        server_onupdate=CURRENT_TIMESTAMP,
    )


class VideoSetting(Base):
    """视频解析 agent 设置持久化，单行（id=1）。

    - ASR 必配：asr_model + asr_api_key（独立于系统模型配置）。
    - Chat 可覆盖：chat_base_url / chat_model / chat_api_key 为空 = 复用系统
      model_relay 配置；非空时用视频 agent 独立配置。
    - api_key 明文存储（内部系统可接受），响应中绝不返回明文，只暴露 has_*。
    """

    __tablename__ = "video_setting"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asr_provider: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'volcengine'"))
    asr_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("'bigmodel'"))
    # 新版控制台单一 Key（X-Api-Key）
    asr_api_key: Mapped[str] = mapped_column(String(1000), nullable=False, server_default=text("''"))
    # 旧版控制台 App ID + Access Token（新版 Key 未配时使用）
    asr_app_id: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"))
    asr_access_token: Mapped[str] = mapped_column(String(1000), nullable=False, server_default=text("''"))
    # 空串 = 复用系统 model_relay
    chat_base_url: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    chat_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"))
    chat_api_key: Mapped[str] = mapped_column(String(1000), nullable=False, server_default=text("''"))
    frames: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("12"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=CURRENT_TIMESTAMP,
        server_onupdate=CURRENT_TIMESTAMP,
    )


class User(Base):
    __tablename__ = "rag_user"
    __table_args__ = (
        CheckConstraint("role IN ('user','account_admin')", name="ck_rag_user_role"),
        CheckConstraint("status IN ('active','disabled','deleting')", name="ck_rag_user_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'user'"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=CURRENT_TIMESTAMP,
        server_onupdate=CURRENT_TIMESTAMP,
    )
    platform_tenant_id: Mapped[str | None] = mapped_column(String(64))
    platform_user_id: Mapped[str | None] = mapped_column(String(64))
    platform_department_id: Mapped[str | None] = mapped_column(String(64))


class PlatformSession(Base):
    __tablename__ = "rag_platform_session"
    __table_args__ = (
        Index("idx_platform_session_expiry", "expires_at"),
        Index("idx_platform_session_user", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    identity_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)


class Session(Base):
    __tablename__ = "rag_session"
    __table_args__ = (
        Index("idx_session_user_expiry", "user_id", "expires_at"),
        Index("idx_session_expiry", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)


class KnowledgeBase(Base):
    __tablename__ = "rag_knowledge_base"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uk_kb_owner"),
        Index("idx_kb_owner", "owner_user_id", "updated_at"),
        CheckConstraint(
            "index_status IN ('ready','rebuilding','failed','deleting')",
            name="ck_rag_kb_index_status",
        ),
        CheckConstraint("chunk_size >= 64", name="ck_rag_kb_chunk_size"),
        CheckConstraint("overlap >= 0 AND overlap < chunk_size", name="ck_rag_kb_overlap"),
        CheckConstraint("top_k >= 1", name="ck_rag_kb_top_k"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_user.id", ondelete="RESTRICT"), nullable=False
    )
    # 平台归属（规范 10 §9.2）：LOCAL 模式为空串/None；平台模式由服务端写入，拒绝请求体覆盖
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("512"))
    overlap: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("50"))
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    active_collection: Mapped[str] = mapped_column(String(200), nullable=False)
    cover_path: Mapped[str | None] = mapped_column(String(1000))
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    index_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'ready'"))
    similarity_threshold: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.20")
    )
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CURRENT_TIMESTAMP, server_onupdate=CURRENT_TIMESTAMP
    )


class Document(Base):
    __tablename__ = "rag_document"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uk_document_owner"),
        ForeignKeyConstraint(
            ["kb_id", "owner_user_id"],
            ["rag_knowledge_base.id", "rag_knowledge_base.owner_user_id"],
            name="fk_document_kb_owner",
            ondelete="CASCADE",
        ),
        Index("idx_document_kb_status", "kb_id", "status"),
        Index("idx_document_owner", "owner_user_id"),
        CheckConstraint(
            "status IN ('pending','running','uploaded','profiling','awaiting_mapping',"
            "'previewing','queued','mapping','chunking','embedding','indexing_lexical',"
            "'indexing_dense','activating','done','done_with_warnings','failed','deleting')",
            name="ck_rag_document_status",
        ),
        CheckConstraint("chunk_count >= 0", name="ck_rag_document_chunk_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(2000), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'upload'"))
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'pending'"))
    tags: Mapped[str | None] = mapped_column(String(2000))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    active_ingest_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "rag_ingest_run.id",
            name="fk_document_active_ingest_run",
            ondelete="SET NULL",
            use_alter=True,
        ),
    )
    processed_path: Mapped[str | None] = mapped_column(String(1000))
    mapping_errors_path: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CURRENT_TIMESTAMP, server_onupdate=CURRENT_TIMESTAMP
    )


class DocumentJob(Base):
    __tablename__ = "rag_document_job"
    __table_args__ = (
        ForeignKeyConstraint(
            ["doc_id", "owner_user_id"],
            ["rag_document.id", "rag_document.owner_user_id"],
            name="fk_document_job_owner",
            ondelete="CASCADE",
        ),
        Index("idx_document_job_claim", "status", "created_at"),
        Index("idx_document_job_document", "doc_id"),
        Index("idx_document_job_owner", "owner_user_id"),
        CheckConstraint("job_type IN ('ingest','delete')", name="ck_rag_document_job_type"),
        CheckConstraint(
            "status IN ('pending','running','done','failed')",
            name="ck_rag_document_job_status",
        ),
        CheckConstraint(
            "stage IS NULL OR stage IN ('parsing','indexing','profiling','awaiting_mapping',"
            "'previewing','queued','mapping','chunking','embedding','indexing_lexical',"
            "'indexing_dense','activating')",
            name="ck_rag_document_job_stage",
        ),
        CheckConstraint("attempts >= 0", name="ck_rag_document_job_attempts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    job_type: Mapped[str] = mapped_column(String(20), nullable=False)
    kb_job_id: Mapped[int | None] = mapped_column(BigInteger)
    target_collection: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    stage: Mapped[str | None] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CURRENT_TIMESTAMP, server_onupdate=CURRENT_TIMESTAMP
    )


class MappingTemplate(Base):
    __tablename__ = "rag_mapping_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CURRENT_TIMESTAMP, server_onupdate=CURRENT_TIMESTAMP
    )


class MappingTemplateVersion(Base):
    __tablename__ = "rag_mapping_template_version"
    __table_args__ = (
        UniqueConstraint("mapping_template_id", "version", name="uk_mapping_template_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mapping_template_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rag_mapping_template.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_json: Mapped[str] = mapped_column(Text, nullable=False)
    structure_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)


class DocumentMapping(Base):
    __tablename__ = "rag_document_mapping"
    __table_args__ = (
        UniqueConstraint("document_id", name="uk_document_mapping_document"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_document.id", ondelete="CASCADE"), nullable=False
    )
    mapping_version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("rag_mapping_template_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_user.id", ondelete="RESTRICT"), nullable=False
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class IngestRun(Base):
    __tablename__ = "rag_ingest_run"
    __table_args__ = (
        CheckConstraint("total_records >= 0", name="ck_ingest_run_total_records"),
        CheckConstraint("processed_records >= 0", name="ck_ingest_run_processed_records"),
        CheckConstraint("error_count >= 0", name="ck_ingest_run_error_count"),
        CheckConstraint("chunk_count >= 0", name="ck_ingest_run_chunk_count"),
        Index("idx_ingest_run_document", "document_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_document.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    processed_records: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_path: Mapped[str | None] = mapped_column(String(1000))
    mapping_errors_path: Mapped[str | None] = mapped_column(String(1000))
    manifest_path: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CURRENT_TIMESTAMP, server_onupdate=CURRENT_TIMESTAMP
    )


class Conversation(Base):
    __tablename__ = "rag_conversation"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uk_conversation_owner"),
        Index("idx_conversation_owner", "owner_user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=CURRENT_TIMESTAMP, server_onupdate=CURRENT_TIMESTAMP
    )


class ConversationKb(Base):
    """会话与知识库的多对多关联（会话可绑定多个知识库，提问时对每个库分别检索后合并）。"""

    __tablename__ = "rag_conversation_kb"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            ["rag_conversation.id"],
            name="fk_conversation_kb_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["kb_id", "owner_user_id"],
            ["rag_knowledge_base.id", "rag_knowledge_base.owner_user_id"],
            name="fk_conversation_kb_kb_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", "kb_id", name="uk_conversation_kb"),
        Index("idx_conversation_kb_kb", "kb_id", "owner_user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)


class Message(Base):
    __tablename__ = "rag_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uk_conversation_sequence"),
        CheckConstraint("role IN ('user','assistant','system')", name="ck_rag_message_role"),
        CheckConstraint(
            "status IN ('streaming','completed','failed')", name="ck_rag_message_status"
        ),
        CheckConstraint("tokens_used >= 0", name="ck_rag_message_tokens"),
        CheckConstraint("sequence >= 0", name="ck_rag_message_sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_conversation.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'completed'"))
    retry_of_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rag_message.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)


class Reference(Base):
    __tablename__ = "rag_reference"
    __table_args__ = (Index("idx_reference_message", "message_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_message.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str | None] = mapped_column(String(160))
    doc_id: Mapped[int | None] = mapped_column(BigInteger)
    doc_title: Mapped[str | None] = mapped_column(String(500))
    # 来源知识库快照（无 FK，kb_name 为冗余字段，供历史引用展示来源）
    kb_id: Mapped[int | None] = mapped_column(BigInteger)
    kb_name: Mapped[str | None] = mapped_column(String(500))
    snippet: Mapped[str | None] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)


class QueryLog(Base):
    __tablename__ = "rag_query_log"
    __table_args__ = (
        ForeignKeyConstraint(
            ["kb_id", "owner_user_id"],
            ["rag_knowledge_base.id", "rag_knowledge_base.owner_user_id"],
            name="fk_query_kb_owner",
            ondelete="CASCADE",
        ),
        Index("idx_query_conversation", "conversation_id"),
        Index("idx_query_owner_time", "owner_user_id", "created_at"),
        Index("idx_query_result", "result_status", "created_at"),
        CheckConstraint(
            "result_status IS NULL OR result_status IN ('success','empty','error')",
            name="ck_rag_query_result",
        ),
        CheckConstraint("chunks_count >= 0", name="ck_rag_query_chunks"),
        CheckConstraint("tokens_used >= 0", name="ck_rag_query_tokens"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rag_conversation.id", ondelete="CASCADE"), nullable=False
    )
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    department_id: Mapped[str | None] = mapped_column(String(64))
    user_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rag_message.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rag_message.id", ondelete="SET NULL")
    )
    chunks_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    result_status: Mapped[str | None] = mapped_column(String(20))
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=CURRENT_TIMESTAMP)

