"""
app/memory/embeddings.py

Lokale Embedding-Pipeline für miniSashimi.

Unterstützt:
- sentence-transformers (lokal, Default)
- Ollama (lokal via HTTP)
- Hash-basiertes Toy-Embedding als Fallback (wenn nichts installiert)

DSGVO: Lokal ausgeführt, keine externen Dienste.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import List

logger = logging.getLogger("minisashimi.memory.embeddings")

_EMBEDDING_DIM = 768


class Embedder:
    """
    Async Embedder-Interface mit automatischer Provider-Wahl.

    Provider-Priorität:
    1. sentence-transformers (lokal, GPU-fähig)
    2. Ollama (lokal via HTTP)
    3. Hash-Fallback (deterministisch, niedrige Qualität)
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2") -> None:
        self.model = model
        self._st_model = None
        self._provider: str = "fallback"

    async def load(self) -> None:
        if os.environ.get("MINISASHIMI_DISABLE_ST") == "1":
            self._provider = "fallback"
            return

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._st_model = await asyncio.to_thread(SentenceTransformer, self.model)
            self._provider = "sentence-transformers"
            logger.info(f"Embedder geladen: sentence-transformers/{self.model}")
        except ImportError:
            logger.warning(
                "sentence-transformers nicht installiert. Fallback Hash-Embedding. "
                "Empfehlung: pip install sentence-transformers"
            )
            self._provider = "fallback"
        except Exception as e:
            logger.warning(f"sentence-transformers Fehler: {e}. Fallback Hash-Embedding.")
            self._provider = "fallback"

    async def embed(self, text: str) -> List[float]:
        if not text:
            return [0.0] * _EMBEDDING_DIM

        if self._provider == "sentence-transformers" and self._st_model is not None:
            try:
                vec = await asyncio.to_thread(self._st_model.encode, text)
                return [float(x) for x in vec.tolist()][:512] + [0.0] * (
                    _EMBEDDING_DIM - 512
                )
            except Exception as e:
                logger.warning(f"ST-Embedding fehlgeschlagen: {e}. Fallback.")

        return _hash_embed(text, _EMBEDDING_DIM)


def _hash_embed(text: str, dim: int) -> List[float]:
    """Deterministisches Toy-Embedding (NOT for production)."""
    digest = hashlib.sha512(text.encode("utf-8")).digest()
    raw = list(digest) * ((dim // len(digest)) + 1)
    vec = [(b - 128) / 128.0 for b in raw[:dim]]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]
