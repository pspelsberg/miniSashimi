"""
app/orchestrator/graph_engine.py

Next-Level-Path #1: LangGraph-Stil State Machine Routing

Inspiriert von LangGraph/AutoGen: Klare State Transitions und Conditional Routing.

Konzepte:
- Node: Agent-Rolle (Thinker, Conductor, Worker, Verifier, ...)
- Edge: Transition mit optionaler Condition (Function)
- State: WorkflowState (passed zwischen Nodes)
- Routing: Conditional Edges für dynamische Pfade

Vorteile:
- Explizite Topologie statt custom JSON-Routing
- Conditional Routing (z.B. bei Security-Critical → Re-Run Worker)
- Visualisierbar als Graph
- State-Inspection jederzeit möglich
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("minisashimi.orchestrator.graph")


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class GraphState:
    """Zentraler, versionierter Workflow-State."""

    workflow_id: str
    client_prompt: str
    current_node: Optional[str] = None
    history: List[str] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    node_status: Dict[str, NodeStatus] = field(default_factory=dict)
    iteration_count: Dict[str, int] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def increment(self, key: str, amount: int = 1) -> int:
        self.iteration_count[key] = self.iteration_count.get(key, 0) + amount
        return self.iteration_count[key]

    def mark(self, node: str, status: NodeStatus) -> None:
        self.node_status[node] = status
        if status == NodeStatus.RUNNING:
            self.current_node = node
            self.history.append(node)


NodeCallable = Callable[[GraphState], Awaitable[GraphState]]
Condition = Callable[[GraphState], bool]


@dataclass
class Node:
    name: str
    handler: NodeCallable
    description: str = ""


@dataclass
class Edge:
    from_node: str
    to_node: str
    condition: Optional[Condition] = None
    label: str = ""


class GraphEngine:
    """
    Graph-basierte Orchestrierungs-Engine.

    Verwendung:
        engine = GraphEngine()
        engine.add_node("thinker", thinker_handler, "Systemarchitektur planen")
        engine.add_node("conductor", conductor_handler, "Tasks generieren")
        engine.add_edge("thinker", "conductor")
        engine.add_edge("conductor", "worker", condition=lambda s: s.get("tasks"))
        engine.add_edge("worker", "verifier", condition=lambda s: s.get("needs_review", True))

        state = GraphState(workflow_id="wf-1", client_prompt="...")
        await engine.run(state)
    """

    def __init__(self, max_iterations_per_node: int = 5) -> None:
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = {}
        self._adj: Dict[str, List[Edge]] = {}
        self._entry: Optional[str] = None
        self.max_iterations = max_iterations_per_node

    # ---------- Builder API ----------

    def add_node(self, name: str, handler: NodeCallable, description: str = "") -> Node:
        if name in self.nodes:
            raise ValueError(f"Node '{name}' bereits vorhanden.")
        node = Node(name=name, handler=handler, description=description)
        self.nodes[name] = node
        self._adj.setdefault(name, [])
        if self._entry is None:
            self._entry = name
        return node

    def add_edge(
        self,
        from_node: str,
        to_node: str,
        condition: Optional[Condition] = None,
        label: str = "",
    ) -> Edge:
        if from_node not in self.nodes:
            raise ValueError(f"Quell-Node '{from_node}' unbekannt.")
        if to_node not in self.nodes:
            raise ValueError(f"Ziel-Node '{to_node}' unbekannt.")
        edge = Edge(from_node=from_node, to_node=to_node, condition=condition, label=label)
        self.edges.setdefault(from_node, []).append(edge)
        self._adj[from_node].append(edge)
        return edge

    def set_entry(self, name: str) -> None:
        if name not in self.nodes:
            raise ValueError(f"Entry-Node '{name}' unbekannt.")
        self._entry = name

    # ---------- Execution ----------

    async def run(self, state: GraphState, max_steps: int = 100) -> GraphState:
        """
        Führt den Graphen aus, startend beim Entry-Node.
        Bricht ab, wenn keine ausgehende Edge matched oder max_steps erreicht.
        """
        if not self._entry:
            raise ValueError("Kein Entry-Node definiert.")

        current = self._entry
        steps = 0

        while current and steps < max_steps:
            steps += 1
            state.mark(current, NodeStatus.RUNNING)

            if state.increment(current) > self.max_iterations:
                logger.warning(
                    f"Node '{current}' überschreitet max_iterations "
                    f"({self.max_iterations}). Abbruch."
                )
                state.mark(current, NodeStatus.FAILED)
                state.metadata["aborted_at"] = current
                state.metadata["abort_reason"] = "max_iterations_exceeded"
                break

            node = self.nodes[current]
            logger.info(f"Graph: executing node '{current}' ({node.description})")

            try:
                state = await node.handler(state)
                state.mark(current, NodeStatus.COMPLETED)
            except Exception as e:
                logger.error(f"Node '{current}' fehlgeschlagen: {e}")
                state.mark(current, NodeStatus.FAILED)
                state.metadata["error_node"] = current
                state.metadata["error_message"] = str(e)
                break

            current = self._next(state, current)
            if current is None:
                logger.info("Graph: kein Folgenode. Workflow beendet.")
                break

        if steps >= max_steps:
            logger.warning(f"Graph: max_steps ({max_steps}) erreicht.")
            state.metadata["aborted_at"] = current
            state.metadata["abort_reason"] = "max_steps_reached"

        return state

    def _next(self, state: GraphState, current: str) -> Optional[str]:
        """Bestimmt den nächsten Node per Conditional Routing."""
        edges = self._adj.get(current, [])
        for edge in edges:
            if edge.condition is None or edge.condition(state):
                if edge.label:
                    logger.info(f"Routing {current} → {edge.to_node} ({edge.label})")
                return edge.to_node
        return None

    # ---------- Introspection ----------

    def topology(self) -> Dict[str, Any]:
        """Liefert eine topologische Repräsentation für Visualisierung/Debugging."""
        return {
            "entry": self._entry,
            "nodes": [
                {"name": n, "description": node.description}
                for n, node in self.nodes.items()
            ],
            "edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    "label": e.label,
                    "conditional": e.condition is not None,
                }
                for edges in self.edges.values()
                for e in edges
            ],
        }

    def mermaid(self) -> str:
        """Gibt den Graph als Mermaid-Diagramm zurück."""
        lines = ["```mermaid", "stateDiagram-v2"]
        if self._entry:
            lines.append(f"    [*] --> {self._entry}")
        for n, node in self.nodes.items():
            lines.append(f"    state {n} : {node.description or n}")
        for edges in self.edges.values():
            for e in edges:
                arrow = f"--> {e.to_node}" if not e.condition else f"--> {e.to_node} : {e.label or 'cond?'}"
                lines.append(f"    {e.from_node} {arrow}")
        lines.append("```")
        return "\n".join(lines)
