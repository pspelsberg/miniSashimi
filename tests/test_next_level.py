"""
tests/test_next_level.py

Tests für die Skill Builder Next-Level-Path Komponenten:
- HybridMemory (Sliding Window + Vector Store)
- SemanticCache (Embeddings-basiert)
- HandoffValidator (Strikte Pydantic-Kontrakte)
- ACPBus (Message Passing)
- GraphEngine (Conditional Routing)
- LLMJudge (Mock-basiert)
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestSemanticCache(unittest.TestCase):
    """Tests für den Semantic Cache."""

    def setUp(self):
        from app.memory.semantic_cache import SemanticCache
        self.cache = SemanticCache(threshold=0.7, ttl_seconds=60, max_entries=100)

    async def async_setup(self):
        await self.cache.start()

    def test_store_and_lookup_identical(self):
        asyncio.run(self.async_setup())

        async def run():
            await self.cache.store(
                "Wie schreibe ich eine REST API in Python?",
                "Verwende Flask oder FastAPI.",
                workflow_id="wf-1",
            )
            hit = await self.cache.lookup(
                "Wie schreibe ich eine REST API in Python?",
                workflow_id="wf-1",
            )
            return hit

        hit = asyncio.run(run())
        self.assertIsNotNone(hit)
        self.assertEqual(hit.response, "Verwende Flask oder FastAPI.")

    def test_lookup_miss_returns_none(self):
        asyncio.run(self.async_setup())

        async def run():
            return await self.cache.lookup("Diese Frage wurde nie gestellt.")

        result = asyncio.run(run())
        self.assertIsNone(result)

    def test_invalidate_workflow(self):
        asyncio.run(self.async_setup())

        async def run():
            await self.cache.store("Frage A", "Antwort A", workflow_id="wf-1")
            await self.cache.store("Frage B", "Antwort B", workflow_id="wf-2")
            count = await self.cache.invalidate(workflow_id="wf-1")
            return count

        n = asyncio.run(run())
        self.assertGreaterEqual(n, 1)


class TestHybridMemory(unittest.TestCase):
    """Tests für Hybrid Memory."""

    def setUp(self):
        from app.memory.hybrid_memory import HybridMemory
        self.tmpdir = tempfile.mkdtemp()
        self.mem = HybridMemory(
            window_size=3, lancedb_uri=self.tmpdir, table_name="test"
        )

    def tearDown(self):
        asyncio.run(self.mem.stop())
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_and_get_window(self):
        async def run():
            await self.mem.start()
            await self.mem.add("Task 1", phase="plan", workflow_id="wf-1")
            await self.mem.add("Task 2", phase="plan", workflow_id="wf-1")
            await self.mem.add("Task 3", phase="build", workflow_id="wf-1")
            window = await self.mem.get_window(workflow_id="wf-1")
            return window

        window = asyncio.run(run())
        self.assertEqual(len(window), 3)

    def test_window_eviction(self):
        async def run():
            await self.mem.start()
            for i in range(5):
                await self.mem.add(f"Task {i}", phase="plan", workflow_id="wf-1")
            window = await self.mem.get_window(workflow_id="wf-1")
            return window

        window = asyncio.run(run())
        self.assertEqual(len(window), 3)

    def test_build_context(self):
        async def run():
            await self.mem.start()
            await self.mem.add("Erste Notiz", phase="plan", workflow_id="wf-1")
            await self.mem.add("API entwerfen", phase="design", workflow_id="wf-1")
            ctx = await self.mem.build_context(
                workflow_id="wf-1", query="API"
            )
            return ctx

        ctx = asyncio.run(run())
        self.assertIn("Aktueller Kontext", ctx)
        self.assertIn("API entwerfen", ctx)


class TestHandoffValidator(unittest.TestCase):
    """Tests für den Handoff Validator."""

    def setUp(self):
        from app.orchestrator.schema_validator import HandoffValidator
        self.validator = HandoffValidator(strict=True)

    def test_validate_thinker(self):
        data = {
            "architecture": "Microservices mit FastAPI",
            "module_breakdown": ["API", "DB", "Auth"],
            "interfaces": ["REST"],
        }
        result = self.validator.validate_thinker(data)
        self.assertEqual(len(result.module_breakdown), 3)

    def test_validate_conductor_normalizes(self):
        data = [
            {
                "title": "Backend bauen",
                "assigned_model": "backend",
                "prompt_payload": "Schreibe Python API",
            },
            {
                "title": "Test schreiben",
                "assigned_model": "test_architect",
                "prompt_payload": "Schreibe pytests",
            },
        ]
        result = self.validator.validate_conductor(data)
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(result.tasks[0].assigned_model.value, "worker_backend")

    def test_validate_conductor_string(self):
        json_str = json.dumps([
            {"title": "Frontend bauen", "assigned_model": "frontend", "prompt_payload": "UI Komponente bauen"},
        ])
        result = self.validator.validate_conductor(json_str)
        self.assertEqual(len(result.tasks), 1)

    def test_validate_worker_invalid_confidence(self):
        data = {"code": "print('hi')", "language": "python", "confidence": 1.5}
        with self.assertRaises(Exception):
            self.validator.validate_worker(data)

    def test_validate_security(self):
        data = {
            "findings": [
                {
                    "severity": "KRITISCH",
                    "title": "SQL Injection",
                    "description": "f-string in SQL query",
                    "cwe_id": "CWE-89",
                    "cvss_score": 9.8,
                }
            ],
            "risk_level": "HIGH",
            "summary": "Kritische SQLi-Lücke gefunden.",
            "compliance": {"dsgvo": True, "owasp": False},
        }
        result = self.validator.validate_security(data)
        self.assertEqual(result.risk_level, "HIGH")
        self.assertEqual(len(result.findings), 1)


class TestACPBus(unittest.TestCase):
    """Tests für den ACP-Bus."""

    def setUp(self):
        from app.orchestrator.acp import ACPBus
        self.bus = ACPBus()
        self.received = []

    def handler(self, msg):
        self.received.append(msg)

    async def async_test_publish_subscribe(self):
        from app.orchestrator.acp import ACPMessage, ACPIntent

        self.bus.subscribe("verifier", self.handler)
        msg = ACPMessage(
            sender="worker",
            recipient="verifier",
            intent=ACPIntent.REQUEST,
            payload={"action": "review"},
            workflow_id="wf-1",
        )
        delivered = await self.bus.publish(msg)
        self.assertEqual(delivered, 1)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0].payload["action"], "review")

    def test_publish_subscribe(self):
        asyncio.run(self.async_test_publish_subscribe())

    async def async_test_broadcast(self):
        from app.orchestrator.acp import ACPMessage, ACPIntent

        received_a = []
        received_b = []
        self.bus.subscribe("a", lambda m: received_a.append(m))
        self.bus.subscribe("b", lambda m: received_b.append(m))
        msg = ACPMessage(
            sender="conductor",
            recipient="*",
            intent=ACPIntent.BROADCAST,
            payload={"event": "started"},
        )
        await self.bus.publish(msg)
        self.assertEqual(len(received_a), 1)
        self.assertEqual(len(received_b), 1)

    def test_broadcast(self):
        asyncio.run(self.async_test_broadcast())

    def test_history_filtered(self):
        from app.orchestrator.acp import ACPMessage, ACPIntent

        async def run():
            for i in range(5):
                msg = ACPMessage(
                    sender="x",
                    recipient="y",
                    intent=ACPIntent.EVENT,
                    payload={"i": i},
                    workflow_id="wf-A" if i % 2 == 0 else "wf-B",
                )
                await self.bus.publish(msg)

            hist_a = self.bus.history(workflow_id="wf-A")
            hist_b = self.bus.history(workflow_id="wf-B")
            return hist_a, hist_b

        a, b = asyncio.run(run())
        self.assertEqual(len(a), 3)
        self.assertEqual(len(b), 2)


class TestGraphEngine(unittest.TestCase):
    """Tests für die Graph Engine."""

    def test_simple_linear_graph(self):
        from app.orchestrator.graph_engine import GraphEngine, GraphState, NodeStatus

        async def node_a(state):
            state.set("a_done", True)
            return state

        async def node_b(state):
            state.set("b_done", True)
            return state

        engine = GraphEngine()
        engine.add_node("a", node_a)
        engine.add_node("b", node_b)
        engine.add_edge("a", "b")
        engine.set_entry("a")

        async def run():
            state = GraphState(workflow_id="wf-1", client_prompt="test")
            return await engine.run(state)

        state = asyncio.run(run())
        self.assertTrue(state.get("b_done"))
        self.assertEqual(state.node_status["a"], NodeStatus.COMPLETED)
        self.assertEqual(state.node_status["b"], NodeStatus.COMPLETED)

    def test_conditional_routing(self):
        from app.orchestrator.graph_engine import GraphEngine, GraphState

        async def check(state):
            state.set("score", 0.95)
            return state

        async def high_path(state):
            state.set("path", "high")
            return state

        async def low_path(state):
            state.set("path", "low")
            return state

        engine = GraphEngine()
        engine.add_node("check", check)
        engine.add_node("high", high_path)
        engine.add_node("low", low_path)
        engine.add_edge("check", "high", condition=lambda s: s.get("score", 0) > 0.9, label="score>0.9")
        engine.add_edge("check", "low", condition=lambda s: s.get("score", 0) <= 0.9, label="score≤0.9")
        engine.set_entry("check")

        async def run():
            state = GraphState(workflow_id="wf-1", client_prompt="test")
            return await engine.run(state)

        state = asyncio.run(run())
        self.assertEqual(state.get("path"), "high")

    def test_mermaid_export(self):
        from app.orchestrator.graph_engine import GraphEngine

        async def n(s): return s

        engine = GraphEngine()
        engine.add_node("x", n, "Test node")
        engine.add_node("y", n)
        engine.add_edge("x", "y")

        mermaid = engine.mermaid()
        self.assertIn("stateDiagram-v2", mermaid)
        self.assertIn("x --> y", mermaid)


class TestLLMJudgeParsing(unittest.TestCase):
    """Tests für LLM-Judge Response-Parsing (ohne externe Calls)."""

    def test_parse_valid_response(self):
        from app.evaluation.llm_judge import _parse_judge_response

        raw = json.dumps({
            "overall_score": 0.85,
            "criteria": [
                {"name": "correctness", "score": 0.9, "reasoning": "ok", "issues": []},
                {"name": "security", "score": 0.6, "reasoning": "needs review", "issues": ["SQLi"]},
            ],
            "summary": "Mostly good.",
            "recommendations": ["Add input validation"],
        })
        result = _parse_judge_response(raw)
        self.assertEqual(result.overall_score, 0.85)
        self.assertEqual(len(result.criteria), 2)

    def test_parse_markdown_wrapped(self):
        from app.evaluation.llm_judge import _parse_judge_response

        raw = "```json\n" + json.dumps({
            "overall_score": 0.7,
            "criteria": [],
            "summary": "OK",
            "recommendations": [],
        }) + "\n```"
        result = _parse_judge_response(raw)
        self.assertEqual(result.overall_score, 0.7)


class TestHumanEvalSample(unittest.TestCase):
    """Tests für HumanEval-Runner (Sample-Probleme)."""

    def test_load_sample(self):
        from app.evaluation.humaneval_runner import HumanEvalRunner
        runner = HumanEvalRunner(model="dummy")
        problems = runner.load_sample_problems()
        self.assertGreaterEqual(len(problems), 1)
        self.assertEqual(problems[0].entry_point, "has_close_elements")

    def test_extract_code_block(self):
        from app.evaluation.humaneval_runner import _extract_code_block
        text = "Hier ist der Code:\n```python\ndef foo():\n    return 1\n```\nFertig."
        result = _extract_code_block(text)
        self.assertEqual(result, "def foo():\n    return 1")


class TestQuantizationRecommend(unittest.TestCase):
    """Tests für QuantizationHelper Recommendations."""

    def test_recommend_gpu(self):
        from app.finetuning.quantization import QuantizationHelper, QuantFormat
        helper = QuantizationHelper()
        rec = helper.recommend_format(target_hardware="gpu")
        self.assertEqual(rec, QuantFormat.AWQ)

    def test_recommend_apple_silicon(self):
        from app.finetuning.quantization import QuantizationHelper, QuantFormat
        helper = QuantizationHelper()
        rec = helper.recommend_format(target_hardware="apple_silicon")
        self.assertEqual(rec, QuantFormat.GGUF)

    def test_recommend_small_size(self):
        from app.finetuning.quantization import QuantizationHelper, QuantFormat
        helper = QuantizationHelper()
        rec = helper.recommend_format(target_hardware="cpu", target_speed_priority=False, max_size_mb=1500)
        self.assertEqual(rec, QuantFormat.GGUF)


class TestDPOConfig(unittest.TestCase):
    """Tests für DPO-Konfiguration."""

    def test_default_config(self):
        from app.finetuning.dpo_trainer import DPOConfig
        cfg = DPOConfig()
        self.assertEqual(cfg.beta, 0.1)
        self.assertEqual(cfg.num_epochs, 3)

    def test_synthetic_preferences_creation(self):
        from app.finetuning.dpo_trainer import DPOTrainer

        good = [{"prompt": "p1", "output": "good code", "quality_score": 0.9}]
        bad = [{"prompt": "p1", "output": "bad code", "quality_score": 0.3}]

        trainer = DPOTrainer(model_name="dummy")
        pairs = trainer.build_synthetic_preferences(good, bad)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].chosen, "good code")
        self.assertEqual(pairs[0].rejected, "bad code")

    def test_synthetic_preferences_mismatch_raises(self):
        from app.finetuning.dpo_trainer import DPOTrainer
        trainer = DPOTrainer(model_name="dummy")
        with self.assertRaises(ValueError):
            trainer.build_synthetic_preferences(
                [{"prompt": "p", "output": "x"}],
                [],
            )


class TestACPMessageSerialization(unittest.TestCase):
    """Tests für ACP-Message JSON-Serialisierung."""

    def test_to_from_json_roundtrip(self):
        from app.orchestrator.acp import ACPMessage, ACPIntent, ACPPriority

        msg = ACPMessage(
            sender="conductor",
            recipient="worker",
            intent=ACPIntent.REQUEST,
            payload={"task_id": "t-1"},
            priority=ACPPriority.HIGH,
            workflow_id="wf-42",
        )
        raw = msg.to_json()
        parsed = ACPMessage.from_json(raw)
        self.assertEqual(parsed.sender, "conductor")
        self.assertEqual(parsed.intent, ACPIntent.REQUEST)
        self.assertEqual(parsed.priority, ACPPriority.HIGH)


if __name__ == "__main__":
    unittest.main()
