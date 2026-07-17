# Skill Builder Next Level Roadmap

> Implementierungsplan basierend auf dem DeepLearning.AI Skill Builder Feedback Report
> Datum: 8.7.2026 — Session: dlai_interview_0707712759b048389d962858257fac61

## Übersicht der Next-Level-Pfade

### 1. Multi-Agent Orchestration & Routing
- **LangGraph/AutoGen**: Strukturiertes Graph-basiertes State Management
- **Pydantic Schema Validation**: Strict Handoff Validation
- **Agent Communication Protocol (ACP)**: Standardisierte Agent-Kommunikation

### 2. Local LLM Fine-Tuning & Optimization
- **LLM-as-a-Judge**: Systematische Evaluation
- **HumanEval Benchmark**: Automated Code-Generation Tests
- **AWQ/GGUF Quantization**: Optimierung lokaler Inferenz
- **DPO (Direct Preference Optimization)**: Post-Training Alignment

### 3. State Management & Vector Databases
- **Hybrid Memory**: Sliding Window + LanceDB
- **Metadata Filtering**: Timestamps, Phase Identifiers
- **Semantic Caching**: Wiederverwendung häufiger Agent-Queries

---

## Implementierungs-Status

### Phase A — Memory Layer ✅ IMPLEMENTIERT

| Komponente | Datei | Status |
|------------|-------|--------|
| LanceDBStore | `app/memory/lancedb_store.py` | ✅ Done |
| Embedder | `app/memory/embeddings.py` | ✅ Done (sentence-transformers + Fallback) |
| HybridMemory | `app/memory/hybrid_memory.py` | ✅ Done |
| SemanticCache | `app/memory/semantic_cache.py` | ✅ Done |

**Features:**
- Sliding Window (konfigurierbare Größe) für aktuelle Konversation
- LanceDB-Anbindung mit Metadata-Filter (Phase, Workflow-ID, Agent, Tags, Timestamps)
- Automatischer In-Memory-Fallback (kein LanceDB verpflichtend)
- Asynchrone Indexierung
- Embedder mit 3 Providern: sentence-transformers, Ollama, Hash-Fallback

### Phase B — Orchestration Layer ✅ IMPLEMENTIERT

| Komponente | Datei | Status |
|------------|-------|--------|
| SchemaValidator | `app/orchestrator/schema_validator.py` | ✅ Done |
| GraphEngine | `app/orchestrator/graph_engine.py` | ✅ Done |
| ACProtocol | `app/orchestrator/acp.py` | ✅ Done |

**Features:**
- Strikte Pydantic-Kontrakte für alle Handoffs (Thinker → Conductor → Worker → Verifier → Security → Synthesis)
- 6 Output-Schemas mit Field-Validation
- Coercion für LLM-Output-Varianten
- Metrics-Tracking
- Graph-Engine mit Conditional Routing (Lambda-Conditions)
- Visualisierung als Mermaid-Diagramm
- Max-Iterations-Schutz gegen Endlosschleifen
- ACP-Bus mit Request/Response/Event/Error/Broadcast Intents
- TTL, PII-Filter, Payload-Größen-Limit (CWE-400)

### Phase C — Evaluation Layer ✅ IMPLEMENTIERT

| Komponente | Datei | Status |
|------------|-------|--------|
| LLMJudge | `app/evaluation/llm_judge.py` | ✅ Done |
| HumanEvalRunner | `app/evaluation/humaneval_runner.py` | ✅ Done |
| QuantizationHelper | `app/finetuning/quantization.py` | ✅ Done |
| DPOTrainer | `app/finetuning/dpo_trainer.py` | ✅ Done |

**Features:**
- LLM-as-a-Judge mit 6 Kriterien (Correctness, Completeness, Style, Security, Efficiency, Clarity)
- Multi-Run-Consensus für Bias-Reduktion
- A/B-Comparison Mode
- HumanEval-Benchmark mit Sandbox-Execution (subprocess + timeout)
- AWQ-Quantisierung (4-bit, group-size konfigurierbar)
- GGUF-Quantisierung (q2_k, q4_k_m, q5_k_m, q8_0)
- Auto-Recommendation basierend auf Hardware
- DPO-Trainer mit synthetischen Präferenz-Paaren
- Export-Pipeline: DPO → GGUF

### Phase D — REST API ✅ IMPLEMENTIERT

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/next-level/judge` | POST | LLM-as-a-Judge ausführen |
| `/api/next-level/humaneval/run` | POST | HumanEval-Benchmark starten |
| `/api/next-level/stats` | GET | Memory + Cache + Validator Stats |
| `/api/next-level/quantize` | POST | AWQ/GGUF-Quantisierung triggern |
| `/api/next-level/dpo` | POST | DPO-Training starten |
| `/api/next-level/dpo/preferences/synthetic` | POST | Synth. Präferenzen erzeugen |
| `/api/next-level/graph/topology` | GET | Graph-Topologie abrufen |
| `/api/next-level/acp/send` | POST | ACP-Message senden |
| `/api/next-level/acp/history` | GET | ACP-History abrufen |
| `/api/next-level/validate` | POST | Handoff-Schema validieren |

### Phase E — Tests ✅ IMPLEMENTIERT

`tests/test_next_level.py` mit **28 Tests** für alle neuen Komponenten:

- SemanticCache: 3 Tests (Store, Lookup, Invalidate)
- HybridMemory: 3 Tests (Add, Eviction, Context-Building)
- HandoffValidator: 5 Tests (Alle 6 Phasen + Coercion)
- ACPBus: 3 Tests (Publish-Subscribe, Broadcast, History-Filter)
- GraphEngine: 3 Tests (Linear, Conditional, Mermaid)
- LLMJudge: 2 Tests (JSON-Parse, Markdown-Wrap)
- HumanEval: 2 Tests (Sample, Code-Extraction)
- Quantization: 3 Tests (Hardware-Recommendations)
- DPO: 3 Tests (Config, Synthetic, Mismatch)
- ACPMessage: 1 Test (Serialization)

**Test-Result: 28/28 passed in 0.29s**

---

## Integration in miniSashimi

Die neuen Komponenten sind in `app/main.py` als globale App-State-Services registriert:

```python
app.state.handoff_validator = HandoffValidator(strict=True)
app.state.acp_bus = ACPBus()
app.state.hybrid_memory = HybridMemory(window_size=20)
app.state.semantic_cache = SemanticCache(threshold=0.92, ttl_seconds=3600)
app.state.llm_judge = LLMJudge(model="openai/gpt-4o", consensus_runs=1)
```

Lifecycle wird über FastAPI's lifespan-Context-Manager verwaltet (start/stop).

---

## Verzeichnisbaum (neu)

```
miniSashimi/
├── app/
│   ├── memory/                       # NEU
│   │   ├── __init__.py
│   │   ├── embeddings.py
│   │   ├── hybrid_memory.py
│   │   ├── lancedb_store.py
│   │   └── semantic_cache.py
│   ├── orchestrator/                 # ERWEITERT
│   │   ├── __init__.py
│   │   ├── acp.py                    # NEU
│   │   ├── engine.py
│   │   ├── graph_engine.py           # NEU
│   │   └── schema_validator.py       # NEU
│   ├── evaluation/                   # NEU
│   │   ├── __init__.py
│   │   ├── humaneval_runner.py
│   │   └── llm_judge.py
│   ├── finetuning/                   # NEU
│   │   ├── __init__.py
│   │   ├── dpo_trainer.py
│   │   └── quantization.py
│   └── adapters/inbound/
│       ├── next_level.py             # NEU (10 Endpoints)
│       └── rest.py
├── tests/
│   └── test_next_level.py            # NEU (28 Tests)
└── SKILL_BUILDER_ROADMAP.md          # NEU
```

---

## Compliance & Security

- **OWASP Top 10:2025**: Path-Traversal-Schutz (CWE-22), SSRF-Schutz (CWE-918), Resource-Limits (CWE-400)
- **DSGVO/GDPR**: Lokale Embeddings, PII-Filter, kein Export
- **EU AI Act**: Audit-Log, Model-Cards via Metadata
- **Cyber Resilience Act**: Input-Validation, sichere Defaults

---

## Optionale Dependencies

Die Module funktionieren mit minimalen Dependencies. Empfohlene Erweiterungen:

| Package | Zweck | Install |
|---------|-------|---------|
| `lancedb pyarrow` | Vector Store | `pip install lancedb pyarrow` |
| `sentence-transformers` | Qualitativ hochwertige Embeddings | `pip install sentence-transformers` |
| `autoawq` | AWQ-Quantisierung | `pip install autoawq` |
| `trl datasets transformers` | DPO-Training | `pip install trl datasets transformers` |

Alle Module haben funktionale Fallbacks, falls Dependencies fehlen.
