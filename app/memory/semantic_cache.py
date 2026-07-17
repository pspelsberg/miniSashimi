"""
app/memory/semantic_cache.py

Next-Level-Path #3: Semantic Caching für Agent-Queries

Speichert LLM-Antworten und findet ähnliche Anfragen über Embedding-Ähnlichkeit.
Reduziert:
- Token-Kosten (kein wiederholter LLM-Aufruf)
- Latenz (Antwort sofort aus Cache)
- Embedding-Berechnungen

Sicherheit:
- TTL (Time-to-Live) verhindert stale Cache
- PII-Filter: PII-haltige Antworten werden nicht gecacht
- Workflow-isoliert oder global nutzbar
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.memory.embeddings import Embedder

logger = logging.getLogger("minisashimi.memory.cache")


@dataclass
class CacheHit:
    key: str
    similarity: float
    response: Any
    created_at: float
    hits: int


class SemanticCache:
    """
    Embedding-basierter Cache für LLM-Antworten.

    Verwendung:
        cache = SemanticCache(threshold=0.92, ttl_seconds=3600)
        await cache.start()

        hit = await cache.lookup("Schreibe REST API")
        if hit:
            return hit.response

        response = await call_llm(...)
        await cache.store(query, response)
    """

    def __init__(
        self,
        threshold: float = 0.92,
        ttl_seconds: int = 3600,
        max_entries: int = 1024,
        scope_global: bool = True,
    ) -> None:
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.scope_global = scope_global

        self._entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._embedder = Embedder()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._stores = 0

    async def start(self) -> None:
        await self._embedder.load()

    async def stop(self) -> None:
        self._entries.clear()

    @staticmethod
    def _make_key(query: str, *, workflow_id: Optional[str], model: str) -> str:
        material = f"{workflow_id or 'global'}|{model}|{query.strip().lower()}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._entries.items() if now - v["created_at"] > self.ttl_seconds]
        for k in expired:
            self._entries.pop(k, None)

    def _evict_lru(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        import math

        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    async def lookup(
        self,
        query: str,
        *,
        workflow_id: Optional[str] = None,
        model: str = "default",
    ) -> Optional[CacheHit]:
        """
        Sucht eine semantisch ähnliche, gecachte Antwort.
        """
        if not query:
            return None

        async with self._lock:
            self._evict_expired()
            if not self._entries:
                self._misses += 1
                return None

            query_vec = await self._embedder.embed(query)
            best: Optional[CacheHit] = None

            for key, entry in self._entries.items():
                if not self.scope_global and entry.get("workflow_id") != workflow_id:
                    continue
                if model and entry.get("model") and entry["model"] != model:
                    continue
                sim = self._cosine(query_vec, entry["vector"])
                if sim >= self.threshold:
                    if best is None or sim > best.similarity:
                        best = CacheHit(
                            key=key,
                            similarity=sim,
                            response=entry["response"],
                            created_at=entry["created_at"],
                            hits=entry["hits"],
                        )

            if best:
                entry = self._entries[best.key]
                entry["hits"] += 1
                self._entries.move_to_end(best.key)
                self._hits += 1
                logger.debug(f"Semantic-Cache HIT (sim={best.similarity:.3f}, hits={entry['hits']})")
                return best
            self._misses += 1
            return None

    async def store(
        self,
        query: str,
        response: Any,
        *,
        workflow_id: Optional[str] = None,
        model: str = "default",
        pii_safe: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Speichert eine Antwort im Cache."""
        if not query or response is None or not pii_safe:
            return ""

        async with self._lock:
            key = self._make_key(query, workflow_id=workflow_id, model=model)
            vec = await self._embedder.embed(query)
            self._entries[key] = {
                "vector": vec,
                "response": response,
                "workflow_id": workflow_id,
                "model": model,
                "created_at": time.time(),
                "hits": 0,
                "metadata": metadata or {},
            }
            self._entries.move_to_end(key)
            self._evict_lru()
            self._stores += 1
            logger.debug(f"Semantic-Cache STORE (key={key[:12]}…, total={len(self._entries)})")
            return key

    async def invalidate(self, *, workflow_id: Optional[str] = None) -> int:
        """Invalidiert Einträge (z.B. nach Workflow-Ende)."""
        async with self._lock:
            if workflow_id is None:
                count = len(self._entries)
                self._entries.clear()
                return count
            keys = [k for k, v in self._entries.items() if v.get("workflow_id") == workflow_id]
            for k in keys:
                self._entries.pop(k, None)
            return len(keys)

    async def stats(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "entries": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "stores": self._stores,
                "hit_rate": (
                    self._hits / (self._hits + self._misses)
                    if (self._hits + self._misses) > 0
                    else 0.0
                ),
                "threshold": self.threshold,
                "ttl_seconds": self.ttl_seconds,
            }
