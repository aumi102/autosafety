"""Phase 6: GraphRAG evidence documents and chunks.

Revision ID: 2025_01_01_0002
Revises: 2025_01_01_0001
Create Date: 2025-01-01 00:02:00.000000

Adds evidence_documents and evidence_chunks tables for Phase 6 GraphRAG
semantic retrieval over complaint and recall text.
"""

from __future__ import annotations

import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "2025_01_01_0002"
down_revision = "2025_01_01_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # evidence_documents table
    op.create_table(
        "evidence_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("document_id", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_entity_id", sa.String(64), nullable=False, index=True),
        sa.Column("source_record_key", sa.String(128), nullable=False, index=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("full_text", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_type", "source_record_key", name="uq_doc_source_key"),
    )
    op.create_index("ix_evidence_documents_source_type", "evidence_documents", ["source_type"])

    # evidence_chunks table
    op.create_table(
        "evidence_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("chunk_id", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_vector_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("embedding_model", sa.String(128), nullable=True),
        sa.Column("embedding_dimension", sa.Integer, nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_doc_index"),
    )
    op.create_index("ix_evidence_chunks_document_id", "evidence_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_table("evidence_chunks")
    op.drop_table("evidence_documents")
