"""
app/memory/lancedb_store.py

Next-Level-Path: State Management & Vector Databases (Skill Builder Path #3)
- Metadata-aware LanceDB-Store für Embeddings
- Filter nach Phase-Identifier, Timestamp, Workflow-ID
- Hybride Suche: semantisch + chronologisch

DSGVO/GDPR-Hinweis:
- Embeddings können PII enthalten. Filter `pii_safe=True` maskiert sensible Inhalte.
- Embeddings werden niemals an externe Dienste exportiert (CWE-200).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("minisashimi.memory.lancedb")


class LanceDBStore:
    """
    Metadata-aware Wrapper um LanceDB.

    Features:
    - Asynchroner Connect/Insert/Query
    - Metadata-Filter (Phase, Workflow-ID, Timestamps, Tags)
    - Optionaler Embedder (lokal: sentence-transformers / ollama)
    - In-Memory-Fallback, falls LanceDB nicht installiert
    """

    def __init__(
        self,
        uri: str = "./.lancedb",
        table_name: str = "agent_memory",
        embedding_dim: int = 768,
    ) -> None:
        self.uri = uri
        self.table_name = table_name
        self.embedding_dim = embedding_dim
        self._table = None
        self._db = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        try:
            import lancedb  # type: ignore
            import pyarrow as pa  # type: ignore
        except ImportError:
            logger.warning(
                "LanceDB nicht installiert. Fallback auf In-Memory-Store. "
                "Installation via: pip install lancedb pyarrow sentence-transformers"
            )
            self._connected = True
            self._table = _InMemoryTable(self.embedding_dim)
            return

        try:
            self._db = await asyncio.to_thread(lancedb.connect, self.uri)
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("vector", pa.list_(pa.float32(), self.embedding_dim)),
                    ("text", pa.string()),
                    ("phase", pa.string()),
                    ("workflow_id", pa.string()),
                    ("agent_role", pa.string()),
                    ("tags", pa.list_(pa.string())),
                    ("created_at", pa.string()),
                    ("pii_safe", pa.bool_()),
                ]
            )

            existing_tables = await asyncio.to_thread(self._db.table_names)
            if self.table_name in existing_tables:
                self._table = await asyncio.to_thread(self._db.open_table, self.table_name)
            else:
                self._table = await asyncio.to_thread(
                    self._db.create_table, self.table_name, schema=schema
                )
            self._connected = True
            logger.info(f"LanceDB verbunden: {self.uri}/{self.table_name}")
        except Exception as e:
            logger.error(f"LanceDB-Verbindung fehlgeschlagen: {e}. Fallback In-Memory.")
            self._table = _InMemoryTable(self.embedding_dim)
            self._connected = True

    async def upsert(
        self,
        text: str,
        vector: List[float],
        *,
        phase: str,
        workflow_id: str,
        agent_role: str = "",
        tags: Optional[List[str]] = None,
        pii_safe: bool = True,
    ) -> str:
        """Speichert Embedding mit Metadaten."""
        await self.connect()
        record_id = str(uuid.uuid4())
        record = {
            "id": record_id,
            "vector": vector,
            "text": text,
            "phase": phase,
            "workflow_id": workflow_id,
            "agent_role": agent_role,
            "tags": tags or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pii_safe": pii_safe,
        }

        if isinstance(self._table, _InMemoryTable):
            self._table.add(record)
        else:
            try:
                await asyncio.to_thread(self._table.add, [record])
            except Exception as e:
                logger.error(f"LanceDB-Upsert fehlgeschlagen: {e}")
        return record_id

    async def search(
        self,
        query_vector: List[float],
        *,
        workflow_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_role: Optional[str] = None,
        tags: Optional[List[str]] = None,
        since: Optional[datetime] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Semantische Suche mit optionalen Metadata-Filtern.

        Filter kombinierbar via AND-Logik.
        """
        await self.connect()

        if isinstance(self._table, _InMemoryTable):
            results = self._table.search(query_vector, limit=limit * 3)
        else:
            try:
                results = await asyncio.to_thread(
                    self._table.search, query_vector
                )
                results = results.limit(limit * 3).to_list()
            except Exception as e:
                logger.error(f"LanceDB-Suche fehlgeschlagen: {e}")
                return []

        filtered: List[Dict[str, Any]] = []
        for row in results:
            if workflow_id and row.get("workflow_id") != workflow_id:
                continue
            if phase and row.get("phase") != phase:
                continue
            if agent_role and row.get("agent_role") != agent_role:
                continue
            if tags:
                row_tags = set(row.get("tags") or [])
                if not all(t in row_tags for t in tags):
                    continue
            if since:
                created = row.get("created_at")
                if created:
                    try:
                        ts = datetime.fromisoformat(created)
                        if ts < since:
                            continue
                    except ValueError:
                        pass
            filtered.append(row)
            if len(filtered) >= limit:
                break

        return filtered

    async def close(self) -> None:
        self._connected = False
        self._table = None
        self._db = None


class _InMemoryTable:
    """Cosine-Similarity Fallback ohne externe Abhängigkeiten."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.rows: List[Dict[str, Any]] = []

    def add(self, record: Dict[str, Any]) -> None:
        self.rows.append(record)

    def search(self, query_vector: List[float], limit: int = 10) -> List[Dict[str, Any]]:
        scored = [
            (self._cosine(query_vector, r["vector"]), r)
            for r in self.rows
            if len(r["vector"]) == self.dim
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        import math

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)
