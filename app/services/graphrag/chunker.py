"""
Deterministic text chunker for evidence documents.

Stable chunk IDs via SHA-256(document_id + chunk_index + chunk_text).
Same input always produces same chunks and IDs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.graphrag.models import chunk_id as _make_chunk_id


def chunk_id(document_id: str, chunk_index: int, chunk_text: str) -> str:
    """Stable chunk ID: SHA-256 of document_id + chunk_index + chunk_text."""
    return _make_chunk_id(document_id, chunk_index, chunk_text)


@dataclass
class Chunk:
    """A single deterministic chunk of an evidence document."""
    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    content_hash: str


def _content_hash(text: str) -> str:
    """SHA-256 of chunk text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_break_point(text: str, start: int, max_end: int) -> int:
    """
    Find a natural break point near max_end.

    Prefers: paragraph break (double newline) > sentence end (.) > word boundary.
    Falls back to max_end if no break found within last 100 chars.
    """
    search_start = max(start, max_end - 100)
    search_text = text[search_start:max_end]

    # Try paragraph break first (double newline)
    for i in range(len(search_text) - 1, -1, -1):
        if search_text[i] == "\n" and i > 0 and search_text[i - 1] == "\n":
            return search_start + i

    # Try sentence end (period)
    for i in range(len(search_text) - 2, -1, -1):
        if search_text[i] == ".":
            return search_start + i + 1

    # Try single newline (line break)
    for i in range(len(search_text) - 1, -1, -1):
        if search_text[i] == "\n":
            return search_start + i + 1

    return max_end


class TextChunker:
    """
    Deterministic text chunker with configurable max length and overlap.

    Guarantees:
    - Same input produces same chunks and IDs
    - Stable chunk IDs
    - No empty chunks
    - Overlapping boundary if configured
    - Short documents remain one chunk
    """

    def __init__(
        self,
        max_length: int = 1000,
        overlap: int = 50,
    ):
        if max_length < 100:
            raise ValueError("max_length must be at least 100")
        if overlap < 0:
            raise ValueError("overlap must be non-negative")
        if overlap >= max_length:
            raise ValueError("overlap must be less than max_length")
        self.max_length = max_length
        self.overlap = overlap

    def chunk(self, document_id: str, text: str) -> list[Chunk]:
        """
        Split text into deterministic chunks.

        For short documents (< max_length), returns a single chunk.
        For longer documents, splits on sentence/paragraph boundaries when possible.
        """
        text = text.strip()
        if not text:
            return []

        # Short documents — single chunk
        if len(text) <= self.max_length:
            cid = chunk_id(document_id, 0, text)
            return [
                Chunk(
                    chunk_id=cid,
                    document_id=document_id,
                    chunk_index=0,
                    text=text,
                    content_hash=_content_hash(text),
                )
            ]

        # Long documents — split with overlap
        chunks: list[Chunk] = []
        start = 0
        index = 0

        while start < len(text):
            end = min(start + self.max_length, len(text))

            if end < len(text):
                # Try to break at a natural boundary
                break_point = _find_break_point(text, start, end)
                if break_point > start:
                    end = break_point

            chunk_text = text[start:end].strip()
            if chunk_text:
                cid = chunk_id(document_id, index, chunk_text)
                chunks.append(
                    Chunk(
                        chunk_id=cid,
                        document_id=document_id,
                        chunk_index=index,
                        text=chunk_text,
                        content_hash=_content_hash(chunk_text),
                    )
                )
                index += 1

            # Advance with overlap — must always make progress
            advance_to = end - self.overlap
            if advance_to <= start:
                # Cannot advance with full overlap without going backward or stalling.
                # Jump to the natural end to collect the remaining text as the last chunk.
                start = end
            else:
                start = advance_to

        return chunks


def chunk_document(
    document_id: str,
    text: str,
    max_length: int = 1000,
    overlap: int = 50,
) -> list[Chunk]:
    """
    Convenience function: chunk a document with default parameters.
    """
    chunker = TextChunker(max_length=max_length, overlap=overlap)
    return chunker.chunk(document_id, text)
