"""Embedding helpers provide SBERT vectors with a deterministic fallback."""

import hashlib
import logging
from threading import Lock
from typing import List

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None  # type: ignore

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Wrap SentenceTransformer usage with fallback hashing."""

    def __init__(self) -> None:
        """Initialise lazy-loaded transformer and state flags."""
        self._model = None
        self._model_lock = Lock()
        self._warned_fallback = False

    def _load_model(self) -> None:
        """Load transformer model once, guarding with a lock."""
        if self._model is not None or SentenceTransformer is None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            try:
                self._model = SentenceTransformer('all-MiniLM-L6-v2')
            except Exception as exc:  # pragma: no cover
                logger.warning('Failed to load SentenceTransformer model: %s', exc)
                self._model = None

    def encode(self, texts: List[str]) -> np.ndarray:
        """Return embeddings for supplied texts, falling back to hash vectors if needed."""
        if not texts:
            # Keep shape consistent with expectation of (n, dim). 32 dims for fallback.
            return np.zeros((0, 32), dtype=np.float32)
        self._load_model()
        if self._model is not None:
            try:
                vectors = self._model.encode(texts, convert_to_numpy=True)
                return vectors.astype(np.float32)
            except Exception as exc:  # pragma: no cover
                logger.warning('SentenceTransformer encode failed, using fallback: %s', exc)
        return self._fallback(texts)

    def _fallback(self, texts: List[str]) -> np.ndarray:
        """Produce deterministic hash-based vectors when transformers are unavailable."""
        if not self._warned_fallback:
            logger.warning('Using hash-based embedding fallback. Semantic accuracy will be reduced.')
            self._warned_fallback = True
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.lower().encode('utf-8')).digest()
            vec = np.frombuffer(digest[:32], dtype=np.uint8).astype(np.float32)
            norm = np.linalg.norm(vec)
            if norm:
                vec = vec / norm
            vectors.append(vec)
        return np.vstack(vectors)


embedding_service = EmbeddingService()
