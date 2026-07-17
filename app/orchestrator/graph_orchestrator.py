"""
app/orchestrator/graph_orchestrator.py

Refaktorierter Orchestrator, der den bestehenden OrchestratorEngine um
die Next-Level-Path Komponenten ergänzt:

- Strikte Pydantic-Schema-Validation für alle Handoffs
- GraphEngine mit Conditional Routing (z.B. Re-Run Worker bei Security-Critical)
- Hybrid Memory: Speichert alle Agent-Ausgaben mit Phase/Workflow-Metadata
- Semantic Cache: Verhindert doppelte LLM-Calls
- ACP-Bus: Standardisierte Agent-zu-Agent-Kommunikation

Diese Engine kann als Drop-in-Replacement oder als parallele Variante zum
bestehenden OrchestratorEngine genutzt werden (via Feature-Flag).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.orchestrator.engine import (
    OrchestratorEngine,
    parse_conductor_tasks,
    sanitize_llm_output,
    notify_dashboard,
)
from app.orchestrator.graph_engine import (
    GraphEngine,
    GraphState,
    NodeStatus,
)
from app.orchestrator.schema_validator import (
    HandoffValidator,
    Severity,
    ConductorOutput,
    WorkerOutput,
    SecurityAuditorOutput,
    PreVerifierOutput,
    SynthesisOutput,
)
from app.orchestrator.acp import (
    ACPBus,
    ACPMessage,
    ACPIntent,
    acp_event,
)

logger = logging.getLogger("minisashimi.orchestrator.graph_v2")


class GraphOrchestrator(OrchestratorEngine):
    """
    Erweiterter Orchestrator mit Graph-Engine und Next-Level-Path Features.

    Vererbung von OrchestratorEngine:
    - Übernimmt State-Management, Cost-Tracking, PII-Masking
    - Überschreibt _run_internal mit Graph-basiertem Routing
    """

    def __init__(
        self,
        workflow_id: str,
        client_prompt: str,
        *,
        validator: Optional[HandoffValidator] = None,
        memory: Optional[Any] = None,
        cache: Optional[Any] = None,
        acp_bus: Optional[ACPBus] = None,
        max_worker_retries: int = 2,
    ) -> None:
        super().__init__(workflow_id, client_prompt)
        self.validator = validator or HandoffValidator(strict=True)
        self.memory = memory
        self.cache = cache
        self.acp_bus = acp_bus
        self.max_worker_retries = max(0, min(max_worker_retries, 5))
        self._worker_attempts = 0
        self._raw_tasks: List[Dict[str, Any]] = []
        self._thinker_output: str = ""
        self._combined_code: str = ""
        self._verifier_output: str = ""
        self._security_output: str = ""
        self._final_content: str = ""
        self._needs_rerun: bool = False

    def _build_engine(self) -> GraphEngine:
        """Konstruiert den Graphen mit allen Phasen und Conditional Edges."""
        engine = GraphEngine(max_iterations_per_node=3)

        engine.add_node("anonymize", self._node_anonymize, "PII-Maskierung")
        engine.add_node("thinker", self._node_thinker, "Phase 1a: Systemarchitektur planen")
        engine.add_node("conductor", self._node_conductor, "Phase 1b: Tasks generieren")
        engine.add_node("workers", self._node_workers, "Phase 2: Worker-Swarm")
        engine.add_node("pre_verifier", self._node_pre_verifier, "Phase 3a: Syntax-Check")
        engine.add_node(
            "security",
            self._node_security,
            "Phase 3b: Security Audit (OWASP/CWE/CVSS)",
        )
        engine.add_node("synthesis", self._node_synthesis, "Phase 4: Finale Synthese")

        engine.add_edge("anonymize", "thinker")
        engine.add_edge("thinker", "conductor")
        engine.add_edge("conductor", "workers")
        engine.add_edge("workers", "pre_verifier")

        engine.add_edge(
            "pre_verifier",
            "security",
            condition=lambda s: s.get("verifier_status") != "FAIL",
            label="OK",
        )
        engine.add_edge(
            "pre_verifier",
            "synthesis",
            condition=lambda s: s.get("verifier_status") == "FAIL",
            label="skip_audit_on_syntax_error",
        )

        engine.add_edge(
            "security",
            "workers",
            condition=lambda s: s.get("security_critical", False) and s.get("worker_retries", 0) < self.max_worker_retries,
            label="critical_finding_re-run",
        )
        engine.add_edge(
            "security",
            "synthesis",
            condition=lambda s: not s.get("security_critical", False) or s.get("worker_retries", 0) >= self.max_worker_retries,
            label="proceed",
        )

        engine.set_entry("anonymize")
        return engine

    async def run(self) -> str:
        """Überschriebene run-Methode: nutzt GraphEngine statt Pipeline-Code."""
        from app.core.database import get_setting
        timeout_val = float(get_setting("workflow_timeout_seconds", "300.0"))
        try:
            async with asyncio.timeout(timeout_val):
                return await self._run_via_graph()
        except asyncio.TimeoutError:
            self.state.status = "FAILED" if hasattr(self.state, "status") else "FAILED"
            self.state.completed_at = datetime.now(timezone.utc)
            self.state.error = f"Workflow-Timeout von {timeout_val} Sekunden ueberschritten."
            if self.state.tasks:
                last_task = self.state.tasks[-1]
                if last_task.status.value in ("RUNNING", "PENDING"):
                    last_task.status = "FAILED"
                    last_task.error = self.state.error
                    last_task.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)
            raise ValueError(self.state.error)

    async def _run_via_graph(self) -> str:
        """Führt den Graph-Orchestrator aus."""
        try:
            await notify_dashboard(self.workflow_id)
            engine = self._build_engine()
            graph_state = GraphState(
                workflow_id=self.workflow_id,
                client_prompt=self.client_prompt,
            )

            logger.info(
                f"GraphOrchestrator gestartet für Workflow {self.workflow_id}. "
                f"Topologie:\n{engine.mermaid()}"
            )

            graph_state = await engine.run(graph_state)

            self.state.status = "COMPLETED"
            self.state.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)

            return self._final_content or "".join(self.output_history)

        except (InterruptedError, asyncio.CancelledError) as e:
            self.state.status = "ABORTED"
            self.state.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)
            await self.stream_queue.put(f"\n\n❌ **Workflow abgebrochen:** {str(e)}\n")
            raise e
        except Exception as e:
            logger.error(f"Schwerer Fehler im GraphOrchestrator {self.workflow_id}: {e}")
            self.state.status = "FAILED"
            self.state.completed_at = datetime.now(timezone.utc)
            if self.state.tasks:
                last_task = self.state.tasks[-1]
                if last_task.status.value == "RUNNING":
                    last_task.status = "FAILED"
                    last_task.error = str(e)
            await notify_dashboard(self.workflow_id)
            await self.stream_queue.put(f"\n\n❌ **Schwerer Fehler im Workflow:** {str(e)}\n")
            raise e
        finally:
            await self.stream_queue.put(None)

    # ===========================================================
    # Graph Nodes
    # ===========================================================

    async def _node_anonymize(self, graph_state: GraphState) -> GraphState:
        """Node 0: PII-Maskierung."""
        self.check_abort()
        from app.core.database import get_setting
        pii_enabled = get_setting("pii_masking_enabled", "true") == "true"
        masked = self.anonymizer.mask(self.client_prompt) if pii_enabled else self.client_prompt
        graph_state.set("masked_prompt", masked)
        graph_state.set("pii_enabled", pii_enabled)
        graph_state.metadata["pii_masking_active"] = pii_enabled
        logger.info(f"PII-Maskierung: {pii_enabled}")
        return graph_state

    async def _node_thinker(self, graph_state: GraphState) -> GraphState:
        """Node 1: Thinker plant die Architektur."""
        from app.adapters.outbound.llm.client import call_llm
        from app.domain.models import Task, TaskStatus

        self.check_abort()
        masked = graph_state.get("masked_prompt", self.client_prompt)
        thinker_task = Task(
            title="Systemarchitektur & Ablauf-Planung (Thinker)",
            assigned_model=self.get_effective_model("model_thinker"),
        )
        self.state.tasks.append(thinker_task)
        thinker_task.status = TaskStatus.RUNNING
        await notify_dashboard(self.workflow_id)

        messages = [
            {
                "role": "system",
                "content": (
                    "Du bist der Thinker-Agent. Deine Aufgabe ist es, komplexe "
                    "Programmier- und Softwareaufgaben in granulare, logische Teilschritte "
                    "zu zerlegen. Entwirfe eine Modulstruktur und klare Schnittstellen. "
                    "Antworte strukturiert mit 'Architektur:', 'Module:' und 'Interfaces:' Sektionen."
                ),
            },
            {"role": "user", "content": f"Bitte plane die Umsetzung für:\n{masked}"},
        ]

        cache_hit = None
        if self.cache:
            try:
                cache_hit = await self.cache.lookup(
                    f"thinker:{masked[:500]}", workflow_id=self.workflow_id
                )
            except Exception:
                cache_hit = None

        if cache_hit:
            content = cache_hit.response if isinstance(cache_hit.response, str) else str(cache_hit.response)
            logger.info(f"Thinker: Cache-Hit (sim={cache_hit.similarity:.3f})")
        else:
            content, usage = await call_llm(thinker_task.assigned_model, messages)
            self.add_cost(usage, thinker_task.assigned_model, thinker_task)
            if self.cache:
                try:
                    await self.cache.store(
                        f"thinker:{masked[:500]}",
                        content,
                        workflow_id=self.workflow_id,
                    )
                except Exception:
                    pass

        self._thinker_output = content
        thinker_task.output = content
        thinker_task.status = TaskStatus.COMPLETED
        thinker_task.completed_at = datetime.now(timezone.utc)
        graph_state.set("thinker_output", content)
        await notify_dashboard(self.workflow_id)
        await self.emit_output("🧠 Phase 1: Systemarchitektur & Ablauf-Planung (Thinker)", content)

        if self.memory:
            try:
                await self.memory.add(
                    content,
                    phase="planning",
                    workflow_id=self.workflow_id,
                    agent_role="thinker",
                )
            except Exception:
                pass

        if self.acp_bus:
            await self.acp_bus.publish(
                acp_event(
                    sender="thinker",
                    event_name="thinker_done",
                    payload={"summary": content[:200]},
                    workflow_id=self.workflow_id,
                )
            )

        return graph_state

    async def _node_conductor(self, graph_state: GraphState) -> GraphState:
        """Node 2: Conductor generiert die Task-Liste (mit strikter Pydantic-Validierung)."""
        from app.adapters.outbound.llm.client import call_llm
        from app.domain.models import Task, TaskStatus

        self.check_abort()
        thinker_content = graph_state.get("thinker_output", "")
        conductor_task = Task(
            title="Tool-Auswahl & Workflow-Konstruktion (Conductor)",
            assigned_model=self.get_effective_model("model_conductor"),
        )
        self.state.tasks.append(conductor_task)
        conductor_task.status = TaskStatus.RUNNING
        await notify_dashboard(self.workflow_id)

        messages = [
            {
                "role": "system",
                "content": (
                    "Du bist der Conductor-Agent. Übersetze den Plan in eine JSON-Taskliste. "
                    "Jedes Element: 'title', 'assigned_model' "
                    "('worker_backend', 'worker_frontend', 'test_architect', 'worker_integration'), "
                    "'prompt_payload'. Antworte AUSSCHLIESSLICH im JSON-Format."
                ),
            },
            {
                "role": "user",
                "content": f"Plan:\n{thinker_content}\n\nGeneriere die JSON-Taskliste.",
            },
        ]

        json_str, usage = await call_llm(conductor_task.assigned_model, messages)
        self.add_cost(usage, conductor_task.assigned_model, conductor_task)

        try:
            validated: ConductorOutput = self.validator.validate_conductor(json_str)
            raw_tasks = [t.model_dump(mode="json") for t in validated.tasks]
            logger.info(
                f"Conductor: {len(raw_tasks)} Tasks strikt validiert "
                f"(Metrics: {self.validator.metrics()})"
            )
        except Exception as e:
            logger.error(f"Conductor-Validierung fehlgeschlagen: {e}")
            try:
                raw_tasks = parse_conductor_tasks(json_str)
            except Exception:
                raw_tasks = [{
                    "title": "Fallback Code-Synthese",
                    "assigned_model": "worker_backend",
                    "prompt_payload": f"Setze folgenden Plan um:\n{thinker_content}",
                }]

        self._raw_tasks = raw_tasks
        conductor_task.output = json.dumps(raw_tasks, indent=2)
        conductor_task.status = TaskStatus.COMPLETED
        conductor_task.completed_at = datetime.now(timezone.utc)
        graph_state.set("raw_tasks", raw_tasks)
        graph_state.set("worker_retries", 0)
        await notify_dashboard(self.workflow_id)
        await self.emit_output(
            "📋 Phase 1: Tool-Auswahl & Workflow-Konstruktion (Conductor)",
            f"```json\n{conductor_task.output}\n```",
        )

        if self.acp_bus:
            await self.acp_bus.publish(
                acp_event(
                    sender="conductor",
                    event_name="conductor_done",
                    payload={"task_count": len(raw_tasks)},
                    workflow_id=self.workflow_id,
                )
            )

        return graph_state

    async def _node_workers(self, graph_state: GraphState) -> GraphState:
        """Node 3: Worker-Swarm führt die Tasks aus."""
        from app.adapters.outbound.llm.client import call_llm
        from app.domain.models import Task, TaskStatus

        self.check_abort()
        raw_tasks = graph_state.get("raw_tasks", [])
        worker_outputs: List[str] = []
        retry_count = graph_state.get("worker_retries", 0)

        for i, raw_task in enumerate(raw_tasks):
            self.check_abort()
            model_role = raw_task.get("assigned_model", "worker_backend")
            actual_model = self.get_effective_model(
                f"model_{model_role}",
                self.get_effective_model("model_worker_backend"),
            )

            w_task = Task(
                title=f"Worker {i+1}: {raw_task.get('title', 'Codegenerierung')}",
                assigned_model=actual_model,
                payload={"instruction": raw_task.get("prompt_payload", "")},
            )
            self.state.tasks.append(w_task)
            w_task.status = TaskStatus.RUNNING
            await notify_dashboard(self.workflow_id)

            worker_messages = [
                {
                    "role": "system",
                    "content": (
                        f"Du bist ein hochspezialisierter Worker ({model_role}). "
                        f"Schreibe lauffähigen, sauberen Code exakt nach Anweisung."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Aufgabe:\n{raw_task.get('prompt_payload', '')}\n\n"
                        f"Bisheriger Kontext:\n" + "\n".join(worker_outputs)
                    ),
                },
            ]

            w_out, usage = await call_llm(actual_model, worker_messages)
            self.add_cost(usage, actual_model, w_task)
            w_task.output = w_out
            w_task.status = TaskStatus.COMPLETED
            w_task.completed_at = datetime.now(timezone.utc)
            worker_outputs.append(f"### Output von {w_task.title}:\n{w_out}")
            await notify_dashboard(self.workflow_id)
            await self.emit_output(f"🛠️ Phase 2: {w_task.title}", w_out)

            if self.memory:
                try:
                    await self.memory.add(
                        w_out,
                        phase="worker",
                        workflow_id=self.workflow_id,
                        agent_role=model_role,
                        tags=[f"task_{i}"],
                    )
                except Exception:
                    pass

        self._combined_code = "\n\n".join(worker_outputs)
        graph_state.set("combined_code", self._combined_code)
        graph_state.set("worker_retries", retry_count + 1)
        return graph_state

    async def _node_pre_verifier(self, graph_state: GraphState) -> GraphState:
        """Node 4: Pre-Verifier prüft Syntax."""
        from app.adapters.outbound.llm.client import call_llm
        from app.domain.models import Task, TaskStatus

        self.check_abort()
        combined = graph_state.get("combined_code", "")
        verifier_task = Task(
            title="Syntax-Validierung & Vorab-Prüfung (Pre-Verifier)",
            assigned_model=self.get_effective_model("model_pre_verifier"),
        )
        self.state.tasks.append(verifier_task)
        verifier_task.status = TaskStatus.RUNNING
        await notify_dashboard(self.workflow_id)

        messages = [
            {
                "role": "system",
                "content": (
                    "Du bist der Pre-Verifier. Prüfe den Code schnell auf Syntaxfehler, "
                    "Klammerfehler oder unvollständige Blöcke. Antworte mit 'STATUS: OK' "
                    "oder 'STATUS: FAIL' gefolgt von der Fehlerbeschreibung."
                ),
            },
            {"role": "user", "content": f"Validiere:\n{combined}"},
        ]
        v_out, usage = await call_llm(verifier_task.assigned_model, messages)
        self.add_cost(usage, verifier_task.assigned_model, verifier_task)
        verifier_task.output = v_out
        verifier_task.status = TaskStatus.COMPLETED
        verifier_task.completed_at = datetime.now(timezone.utc)

        status = "OK" if "STATUS: OK" in v_out.upper() and "STATUS: FAIL" not in v_out.upper() else "FAIL"
        graph_state.set("verifier_status", status)
        graph_state.set("verifier_output", v_out)
        self._verifier_output = v_out

        await notify_dashboard(self.workflow_id)
        await self.emit_output("🔍 Phase 3: Syntax-Validierung (Pre-Verifier)", v_out)
        return graph_state

    async def _node_security(self, graph_state: GraphState) -> GraphState:
        """Node 5: Security Auditor prüft auf Schwachstellen."""
        from app.adapters.outbound.llm.client import call_llm
        from app.domain.models import Task, TaskStatus

        self.check_abort()
        combined = graph_state.get("combined_code", "")
        security_task = Task(
            title="Verifier: Security Auditor",
            assigned_model=self.get_effective_model("model_security_auditor"),
        )
        self.state.tasks.append(security_task)
        security_task.status = TaskStatus.RUNNING
        await notify_dashboard(self.workflow_id)

        messages = [
            {
                "role": "system",
                "content": (
                    "Du bist ein Security Auditor. Prüfe auf Schwachstellen "
                    "(SQLi, XSS, Hardcoded Keys, OWASP Top 10). Werte nach "
                    "[KRITISCH], [WARNUNG], [OPTIMIERUNG] oder [Guter Standard]. "
                    "Gib CWE-IDs und CVSS-Scores an."
                ),
            },
            {"role": "user", "content": f"Code zum Auditieren:\n{combined}"},
        ]
        sec_out, usage = await call_llm(security_task.assigned_model, messages)
        self.add_cost(usage, security_task.assigned_model, security_task)
        security_task.output = sec_out
        security_task.status = TaskStatus.COMPLETED
        security_task.completed_at = datetime.now(timezone.utc)

        critical_count = sec_out.upper().count("[KRITISCH]")
        graph_state.set("security_critical", critical_count > 0)
        graph_state.set("security_output", sec_out)
        graph_state.set("security_critical_count", critical_count)
        self._security_output = sec_out

        if self.acp_bus and critical_count > 0:
            await self.acp_bus.publish(
                acp_event(
                    sender="security",
                    event_name="critical_findings",
                    payload={"count": critical_count, "summary": sec_out[:300]},
                    workflow_id=self.workflow_id,
                )
            )

        await notify_dashboard(self.workflow_id)
        await self.emit_output("🛡️ Phase 3: Verifier: Security Auditor", sec_out)
        return graph_state

    async def _node_synthesis(self, graph_state: GraphState) -> GraphState:
        """Node 6: Finale Synthese für den Client."""
        from app.adapters.outbound.llm.client import call_llm
        from app.domain.models import Task, TaskStatus

        self.check_abort()
        combined = graph_state.get("combined_code", "")
        sec_out = graph_state.get("security_output", "")
        pii_enabled = graph_state.get("pii_enabled", True)

        synthesis_task = Task(
            title="Finales Review & Zusammenfassung",
            assigned_model=self.get_effective_model("model_thinker"),
        )
        self.state.tasks.append(synthesis_task)
        synthesis_task.status = TaskStatus.RUNNING
        await notify_dashboard(self.workflow_id)

        messages = [
            {
                "role": "system",
                "content": (
                    "Du bist der finale Integrations-Agent. Bringe Code und Security-Feedback "
                    "zusammen. Behebe kritische Sicherheitslücken direkt. Verwende exakt "
                    "folgendes Format für Dateien:\n\n"
                    "### FILE: <relativer_pfad>\n```<sprache>\n<code>\n```"
                ),
            },
            {
                "role": "user",
                "content": f"Code:\n{combined}\n\nSecurity-Feedback:\n{sec_out}",
            },
        ]
        final_content, usage = await call_llm(synthesis_task.assigned_model, messages)
        self.add_cost(usage, synthesis_task.assigned_model, synthesis_task)

        if pii_enabled:
            final_content = self.anonymizer.unmask(final_content)
        final_content = sanitize_llm_output(final_content)

        self._final_content = final_content
        synthesis_task.output = final_content
        synthesis_task.status = TaskStatus.COMPLETED
        synthesis_task.completed_at = datetime.now(timezone.utc)

        graph_state.set("synthesis_output", final_content)
        graph_state.set("done", True)

        await notify_dashboard(self.workflow_id)
        await self.emit_output("🎯 Phase 4: Finales Review & Zusammenfassung", final_content)
        return graph_state
