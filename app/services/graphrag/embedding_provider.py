"""
Embedding provider abstraction.

Provides a consistent interface over different embedding backends.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import get_settings


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name of the embedding model."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension."""
        ...

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        ...

    def _normalize(self, vector: list[float]) -> list[float]:
        """L2-normalize a vector."""
        magnitude = math.sqrt(sum(x * x for x in vector))
        if magnitude == 0:
            return vector
        return [x / magnitude for x in vector]


class DeterministicTestProvider(EmbeddingProvider):
    """
    Deterministic test embedding provider.

    Produces stable, reproducible vectors from text using a hash-based
    construction. NOT semantically meaningful — for tests only.

    Properties:
    - Same text always produces the same vector
    - Vectors are L2-normalized
    - Dimension: 384 (compatible with lightweight production models)
    - No network, no API key required
    """

    def __init__(self, dimension: int = 384):
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return "deterministic-test-v1"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_text(self, text: str) -> list[float]:
        """
        Deterministic lexical embedding via token n-gram hashing.

        Tokenizes normalized text, hashes each token, accumulates into a
        fixed-dimension vector. Related texts sharing tokens produce
        overlapping hash contributions -> higher similarity.
        """
        vector = [0.0] * self._dimension
        normalized = self._normalize_text(text)
        tokens = self._tokenize(normalized)

        for token in tokens:
            token_hash = hashlib.sha256(token.encode("utf-8")).digest()
            n_bins = min(len(token_hash) * 8, self._dimension)
            for bit_pos in range(n_bins):
                byte_idx = bit_pos // 8
                bit_idx = bit_pos % 8
                if byte_idx < len(token_hash):
                    if (token_hash[byte_idx] >> (7 - bit_idx)) & 1:
                        vector[bit_pos] += 1.0

        return self._normalize(vector)

    def _normalize_text(self, text: str) -> str:
        """Lowercase, strip punctuation, collapse whitespace."""
        import re
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize into unigrams and bigrams."""
        words = text.split()
        tokens = words[:]
        for i in range(len(words) - 1):
            tokens.append(f"{words[i]}_{words[i + 1]}")
        return tokens


class LocalSentenceTransformerProvider(EmbeddingProvider):
    """
    Local sentence-transformers provider.

    Requires: sentence-transformers package.
    Downloads model on first call if not cached.

    Configuration via GRAPHRAG_EMBEDDING_MODEL and GRAPHRAG_EMBEDDING_DIMENSION env vars.
    """

    def __init__(
        self,
        model_name: str | None = None,
        dimension: int | None = None,
        device: str = "cpu",
    ):
        self._model_name = model_name or "all-MiniLM-L6-v2"
        self._dimension = dimension or 384
        self._device = device
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _load_model(self) -> Any:
        """Lazy-load and return the sentence-transformer model."""
        if self._model is None:
            try:
                # Optional dependency: this provider is only selected when the
                # operator configures it, so the package is not a project
                # requirement and has no stubs.
                from sentence_transformers import (  # type: ignore[import-not-found]
                    SentenceTransformer,
                )
                self._model = SentenceTransformer(self._model_name, device=self._device)
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                ) from exc
        return self._model

    def embed_text(self, text: str) -> list[float]:
        model = self._load_model()
        embedding = model.encode(text, normalize_embeddings=True)
        values: list[float] = embedding.tolist()
        return values


def get_embedding_provider() -> EmbeddingProvider:
    """
    Factory: resolve embedding provider from configuration.

    Reads GRAPHRAG_EMBEDDING_PROVIDER env var.
    Falls back to DeterministicTestProvider when no real model is configured.
    """
    try:
        settings = get_settings()
        provider_name = getattr(settings, "GRAPHRAG_EMBEDDING_PROVIDER", "deterministic")
    except Exception:
        provider_name = "deterministic"

    if provider_name == "sentence_transformers":
        return LocalSentenceTransformerProvider()
    return DeterministicTestProvider(dimension=384)
