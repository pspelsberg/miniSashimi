"""
tests/test_graph_orchestrator.py

Integrationstest für den GraphOrchestrator mit Mock-LLMs.
Testet:
- PII-Anonymisierung (Node 0)
- Thinker → Conductor → Worker → Pre-Verifier → Security → Synthesis Flow
- Conditional Routing (security_critical → Re-Run Worker)
- Schema-Validierung in jedem Schritt
- Hybrid Memory + Semantic Cache Integration
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestGraphOrchestratorLinear(unittest.TestCase):
    """Testet den GraphOrchestrator mit linearem Flow (keine Re-Runs)."""

    def setUp(self):
        from app.orchestrator.graph_orchestrator import GraphOrchestrator
        from app.orchestrator.schema_validator import HandoffValidator
        from app.orchestrator.acp import ACPBus
        from app.memory.hybrid_memory import HybridMemory
        from app.memory.semantic_cache import SemanticCache

        self.tmpdir = tempfile.mkdtemp()
        self.GraphOrchestrator = GraphOrchestrator
        self.validator = HandoffValidator(strict=True)
        self.acp = ACPBus()
        self.memory = HybridMemory(window_size=5, lancedb_uri=self.tmpdir)
        self.cache = SemanticCache(threshold=0.7, ttl_seconds=60)

    def tearDown(self):
        import shutil
        asyncio.run(self.memory.stop())
        asyncio.run(self.cache.stop())
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_linear_flow(self):
        async def run():
            await self.memory.start()
            await self.cache.start()
            engine = self.GraphOrchestrator(
                "wf-test-1",
                "Bau eine REST API mit Flask",
                validator=self.validator,
                memory=self.memory,
                cache=self.cache,
                acp_bus=self.acp,
            )

            mock_responses = {
                "thinker": "Architektur: Flask-App mit 3 Endpoints. Module: app.py, models.py, routes.py.",
                "conductor": json.dumps([
                    {"title": "Flask App", "assigned_model": "backend", "prompt_payload": "Schreibe Flask-App mit /users Endpoint"},
                ]),
                "worker": "```python\nfrom flask import Flask\napp = Flask(__name__)\n```",
                "pre_verifier": "STATUS: OK",
                "security": "Keine kritischen Findings. [Guter Standard] Saubere Implementierung.",
                "synthesis": "### FILE: app.py\n```python\nfrom flask import Flask\napp = Flask(__name__)\n```",
            }

            async def mock_call_llm(model, messages, **kwargs):
                sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
                if "Thinker" in sys_content:
                    return mock_responses["thinker"], {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
                if "Conductor" in sys_content:
                    return mock_responses["conductor"], {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120}
                if "Worker" in sys_content or "spezialisierter" in sys_content:
                    return mock_responses["worker"], {"prompt_tokens": 200, "completion_tokens": 150, "total_tokens": 350}
                if "Pre-Verifier" in sys_content or "Pre-Verifier" in str(messages):
                    return mock_responses["pre_verifier"], {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
                if "Security" in sys_content:
                    return mock_responses["security"], {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230}
                if "Integrations-Agent" in sys_content or "Systhese" in sys_content or "Integration" in sys_content:
                    return mock_responses["synthesis"], {"prompt_tokens": 300, "completion_tokens": 200, "total_tokens": 500}
                return "OK", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

            with patch("app.adapters.outbound.llm.client.call_llm", side_effect=mock_call_llm):
                result = await engine.run()
                return result, engine

            return None, engine

        result, engine = asyncio.run(run())
        self.assertIsNotNone(result)
        self.assertEqual(engine.state.status, "COMPLETED")

    def test_conditional_routing_critical_finding(self):
        async def run():
            await self.memory.start()
            await self.cache.start()
            engine = self.GraphOrchestrator(
                "wf-test-2",
                "Bau eine Login-Seite",
                validator=self.validator,
                memory=self.memory,
                cache=self.cache,
                acp_bus=self.acp,
                max_worker_retries=2,
            )

            worker_calls = []

            async def mock_call_llm(model, messages, **kwargs):
                sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
                if "Thinker" in sys_content:
                    return "Plan: Login mit Username/Passwort", {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
                if "Conductor" in sys_content:
                    return json.dumps([{"title": "Login bauen", "assigned_model": "backend", "prompt_payload": "Login"}]), {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}
                if "Worker" in sys_content or "spezialisierter" in sys_content:
                    worker_calls.append(sys_content[:50])
                    return "```python\n# buggy code\n```", {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}
                if "Pre-Verifier" in sys_content:
                    return "STATUS: OK", {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
                if "Security" in sys_content:
                    return "[KRITISCH] SQL-Injection in Login-Funktion! CWE-89, CVSS 9.8", {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}
                if "Integrations-Agent" in sys_content or "Integration" in sys_content:
                    return "### FILE: login.py\n```python\n# fixed\n```", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
                return "OK", {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}

            with patch("app.adapters.outbound.llm.client.call_llm", side_effect=mock_call_llm):
                await engine.run()
                return len(worker_calls), engine

            return 0, engine

        worker_calls_count, engine = asyncio.run(run())
        self.assertGreaterEqual(
            worker_calls_count, 2,
            f"Worker sollte nach kritischem Finding erneut laufen. Calls: {worker_calls_count}"
        )
        self.assertLessEqual(
            worker_calls_count, 3,
            f"Worker sollte nicht öfter als max_worker_retries+1 laufen. Calls: {worker_calls_count}"
        )


class TestGraphOrchestratorSchemaValidation(unittest.TestCase):
    """Testet, dass Schema-Validierung im Conductor greift."""

    def test_conductor_validation_failure_fallback(self):
        from app.orchestrator.graph_orchestrator import GraphOrchestrator
        from app.orchestrator.schema_validator import HandoffValidator

        async def run():
            engine = GraphOrchestrator(
                "wf-test-3",
                "Test",
                validator=HandoffValidator(strict=True),
            )

            with patch.object(engine, "_node_thinker") as mock_thinker:
                async def fake_thinker(state):
                    state.set("thinker_output", "Plan")
                    return state
                mock_thinker.side_effect = fake_thinker

                with patch("app.adapters.outbound.llm.client.call_llm") as mock_llm:
                    mock_llm.return_value = (
                        "KEIN VALIDES JSON !!!",
                        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    )
                    from app.orchestrator.graph_engine import GraphState
                    state = GraphState(workflow_id="wf-test-3", client_prompt="Test")
                    state.set("masked_prompt", "Test")
                    result = await engine._node_conductor(state)
                    return result, engine

            return None, engine

        state, engine = asyncio.run(run())
        self.assertIn("raw_tasks", state.variables)
        raw_tasks = state.get("raw_tasks", [])
        self.assertGreater(len(raw_tasks), 0)
        self.assertEqual(raw_tasks[0]["assigned_model"], "worker_backend")


class TestGraphOrchestratorConditionalEdges(unittest.TestCase):
    """Testet die Conditional Edges isoliert."""

    def test_syntax_fail_skips_security(self):
        from app.orchestrator.graph_engine import GraphEngine, GraphState

        async def n1(s): return s
        async def n2(s): return s
        async def n3(s): return s

        engine = GraphEngine()
        engine.add_node("a", n1)
        engine.add_node("b", n2)
        engine.add_node("c", n3)
        engine.add_edge("a", "b", condition=lambda s: s.get("verifier_status") != "FAIL")
        engine.add_edge("a", "c", condition=lambda s: s.get("verifier_status") == "FAIL")
        engine.set_entry("a")

        async def run_fail():
            state = GraphState(workflow_id="wf", client_prompt="test")
            state.set("verifier_status", "FAIL")
            return await engine.run(state)

        async def run_ok():
            state = GraphState(workflow_id="wf", client_prompt="test")
            state.set("verifier_status", "OK")
            return await engine.run(state)

        state_fail = asyncio.run(run_fail())
        self.assertEqual(state_fail.current_node, "c")
        self.assertNotIn("b", state_fail.node_status)

        state_ok = asyncio.run(run_ok())
        self.assertEqual(state_ok.current_node, "b")
        self.assertNotIn("c", state_ok.node_status)


class TestGraphOrchestratorACPEvents(unittest.TestCase):
    """Testet ACP-Event-Emission."""

    def test_acp_events_on_thinker_done(self):
        from app.orchestrator.graph_orchestrator import GraphOrchestrator
        from app.orchestrator.acp import ACPBus

        async def run():
            bus = ACPBus()
            engine = GraphOrchestrator(
                "wf-acp-1",
                "Test",
                acp_bus=bus,
            )

            events_received = []

            def handler(msg):
                if msg.intent.value == "event":
                    events_received.append(msg.payload)

            # Subscribe an spezifischem Recipient UND Broadcast
            bus.subscribe("thinker", handler)
            bus.subscribe("*", handler)

            with patch("app.adapters.outbound.llm.client.call_llm") as mock_llm:
                mock_llm.return_value = (
                    "Plan: Test",
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )

                from app.orchestrator.graph_engine import GraphState
                state = GraphState(workflow_id="wf-acp-1", client_prompt="Test")
                state.set("masked_prompt", "Test")
                await engine._node_thinker(state)
                return events_received, bus

            return [], bus

        events, bus = asyncio.run(run())
        self.assertGreater(len(events), 0)
        self.assertTrue(
            any(e.get("event") == "thinker_done" for e in events),
            f"Kein thinker_done Event: {events}"
        )


if __name__ == "__main__":
    unittest.main()
