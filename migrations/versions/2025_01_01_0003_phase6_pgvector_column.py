"""Phase 6 pgvector column.

Revision ID: 2025_01_01_0003
Revises: 2025_01_01_0002
Create Date: 2025-01-01 00:03:00.000000

Adds a pgvector column to evidence_chunks and migrates existing JSONB
embeddings. Enables efficient cosine distance queries.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "2025_01_01_0003"
down_revision = "2025_01_01_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add vector column using pgvector's vector type (384-dim)
    # Requires pgvector extension already enabled on the server
    op.execute("""
        ALTER TABLE evidence_chunks
        ADD COLUMN embedding_vector vector(384)
    """)

    # Migrate existing JSONB embeddings to the pgvector column
    op.execute("""
        UPDATE evidence_chunks
        SET embedding_vector = (
            SELECT ('[' || string_agg(value::text, ',') || ']')::vector
            FROM jsonb_array_elements_text(metadata_json->'embedding')
        )
        WHERE metadata_json ? 'embedding'
          AND metadata_json->'embedding' IS NOT NULL
          AND jsonb_typeof(metadata_json->'embedding') = 'array'
    """)

    # Create HNSW index for fast approximate nearest-neighbor search
    op.execute("""
        CREATE INDEX ix_evidence_chunks_embedding_vector_hnsw
        ON evidence_chunks
        USING hnsw (embedding_vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        WHERE embedding_vector IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_evidence_chunks_embedding_vector_hnsw", table_name="evidence_chunks")
    op.drop_column("evidence_chunks", "embedding_vector")
