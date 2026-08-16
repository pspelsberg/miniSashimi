"""
app/memory/hybrid_memory.py

Next-Level-Path #3: Hybrid Memory Architecture

Kombiniert:
- Sliding Window: Aktuelle Konversationsschritte im Volltext
- Vector Store (LanceDB): Ältere Schritte als Embeddings (semantische Suche)
- Metadata Filter: Phase, Workflow-ID, Timestamps
- Asynchrone Indexierung nach Schwellwert

Vorteile:
- Begrenztes Context-Window für Worker
- Vollständige Historie via semantische Suche abrufbar
- Chronologische Treue bleibt erhalten
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from app.memory.embeddings import Embedder
from app.memory.lancedb_store import LanceDBStore

logger = logging.getLogger("minisashimi.memory.hybrid")


class MemoryEntry:
    """Ein einzelner Memory-Eintrag."""

    __slots__ = (
        "id",
        "text",
        "phase",
        "workflow_id",
        "agent_role",
        "tags",
        "created_at",
        "embedding",
        "in_window",
    )

    def __init__(
        self,
        text: str,
        *,
        phase: str,
        workflow_id: str,
        agent_role: str = "",
        tags: Optional[List[str]] = None,
        embedding: Optional[List[float]] = None,
    ) -> None:
        self.id = f"{workflow_id}:{int(time.time() * 1000)}:{id(self)}"
        self.text = text
        self.phase = phase
        self.workflow_id = workflow_id
        self.agent_role = agent_role
        self.tags = tags or []
        self.created_at = datetime.now(timezone.utc)
        self.embedding = embedding or []
        self.in_window = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "phase": self.phase,
            "workflow_id": self.workflow_id,
            "agent_role": self.agent_role,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
        }


class HybridMemory:
    """
    Hybrid-Memory-Architektur: Sliding Window + Vector Store.

    Nutzung:
        mem = HybridMemory(window_size=20)
        await mem.start()
        await mem.add("Task: Build API", phase="planning", workflow_id="wf-1")
        context = await mem.build_context(workflow_id="wf-1", query="...")
    """

    # F-005: Capacity limit to prevent OOM under sustained workflow load (CWE-400)
    MAX_MEMORY_ENTRIES = 10_000

    def __init__(
        self,
        window_size: int = 20,
        max_entries: int = MAX_MEMORY_ENTRIES,
        lancedb_uri: str = "./.lancedb",
        table_name: str = "agent_memory",
    ) -> None:
        self.window_size = window_size
        self.max_entries = max(window_size, max_entries)
        self._window: Deque[MemoryEntry] = deque(maxlen=window_size)
        self._all: Deque[MemoryEntry] = deque()
        self._store = LanceDBStore(uri=lancedb_uri, table_name=table_name)
        self._embedder = Embedder()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self._store.connect()
        await self._embedder.load()
        logger.info(
            f"HybridMemory gestartet (window={self.window_size}, "
            f"embedder={self._embedder._provider})"
        )

    async def stop(self) -> None:
        await self._store.close()

    async def add(
        self,
        text: str,
        *,
        phase: str,
        workflow_id: str,
        agent_role: str = "",
        tags: Optional[List[str]] = None,
        pii_safe: bool = True,
    ) -> str:
        """Fügt einen Eintrag hinzu (Sliding Window + Vector Index)."""
        if not text:
            return ""

        async with self._lock:
            embedding = await self._embedder.embed(text)
            entry = MemoryEntry(
                text=text,
                phase=phase,
                workflow_id=workflow_id,
                agent_role=agent_role,
                tags=tags or [],
                embedding=embedding,
            )
            self._all.append(entry)
            self._window.append(entry)

            if len(self._all) > self.window_size:
                evicted = self._all[-(self.window_size + 1)]
                if evicted in self._window:
                    pass
                await self._store.upsert(
                    text=evicted.text,
                    vector=evicted.embedding,
                    phase=evicted.phase,
                    workflow_id=evicted.workflow_id,
                    agent_role=evicted.agent_role,
                    tags=evicted.tags,
                    pii_safe=pii_safe,
                )

            # F-005: Enforce capacity limit — evict oldest entries into vector store (CWE-400)
            while len(self._all) > self.max_entries:
                oldest = self._all.popleft()
                if oldest.embedding:
                    await self._store.upsert(
                        text=oldest.text,
                        vector=oldest.embedding,
                        phase=oldest.phase,
                        workflow_id=oldest.workflow_id,
                        agent_role=oldest.agent_role,
                        tags=oldest.tags,
                        pii_safe=pii_safe,
                    )

            return entry.id

    async def get_window(self, workflow_id: Optional[str] = None) -> List[MemoryEntry]:
        """Gibt aktuelle Window-Einträge zurück (optional gefiltert)."""
        async with self._lock:
            items = list(self._window)
        if workflow_id:
            items = [e for e in items if e.workflow_id == workflow_id]
        return items

    async def search(
        self,
        query: str,
        *,
        workflow_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_role: Optional[str] = None,
        tags: Optional[List[str]] = None,
        since: Optional[datetime] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Semantische Suche über die gesamte Historie (Window + Vector Store).
        """
        async with self._lock:
            window_hits = [
                e.to_dict() for e in self._window
                if (not workflow_id or e.workflow_id == workflow_id)
                and (not phase or e.phase == phase)
                and (not agent_role or e.agent_role == agent_role)
                and (not since or e.created_at >= since)
            ]

        if not query:
            return window_hits[:limit]

        vec = await self._embedder.embed(query)
        store_hits = await self._store.search(
            vec,
            workflow_id=workflow_id,
            phase=phase,
            agent_role=agent_role,
            tags=tags,
            since=since,
            limit=limit,
        )

        seen = {h["id"] for h in window_hits}
        merged = list(window_hits)
        for hit in store_hits:
            if hit["id"] not in seen:
                merged.append(
                    {
                        "id": hit["id"],
                        "text": hit["text"],
                        "phase": hit.get("phase", ""),
                        "workflow_id": hit.get("workflow_id", ""),
                        "agent_role": hit.get("agent_role", ""),
                        "tags": hit.get("tags", []),
                        "created_at": hit.get("created_at", ""),
                    }
                )
                seen.add(hit["id"])

        return merged[:limit]

    async def build_context(
        self,
        workflow_id: str,
        query: str,
        *,
        window_size: Optional[int] = None,
        retrieval_limit: int = 5,
    ) -> str:
        """
        Baut einen LLM-Kontext: Aktuelles Window + semantische Retrieval-Treffer.
        """
        window = await self.get_window(workflow_id=workflow_id)
        window_size = window_size or self.window_size
        recent = window[-window_size:]

        retrieved: List[Dict[str, Any]] = []
        if query:
            retrieved = await self.search(
                query, workflow_id=workflow_id, limit=retrieval_limit
            )

        lines = ["# Aktueller Kontext (Sliding Window)"]
        for entry in recent:
            lines.append(
                f"[{entry.created_at.isoformat()}] "
                f"[{entry.phase}/{entry.agent_role}] {entry.text}"
            )

        if retrieved:
            lines.append("\n# Semantisch ähnliche Historie")
            for r in retrieved:
                lines.append(
                    f"[{r.get('created_at', '?')}] "
                    f"[{r.get('phase', '?')}/{r.get('agent_role', '?')}] "
                    f"{r.get('text', '')}"
                )

        return "\n".join(lines)

    async def stats(self) -> Dict[str, Any]:
        return {
            "window_size": self.window_size,
            "window_filled": len(self._window),
            "total_entries": len(self._all),
            "embedder": self._embedder._provider,
        }
