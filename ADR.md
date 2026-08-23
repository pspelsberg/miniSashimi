# ADR: miniSashimi – Intelligenter Multi-Agenten-Reverse-Proxy mit Hexagoner Architektur

- **Status:** Angenommen (Accepted)
- **Datum:** 2026-08-21
- **Kontext:** miniSashimi – OpenAI-kompatibler Gateway-Proxy für Coding-Agenten (z. B. PI Coding Agent) mit PII-Maskierung, Multi-LLM-Routing, Docker-Sandbox und Live-Dashboard
- **Entscheidungsträger:** Architecture & AI Infrastructure Team

---

## 1. Kontext & Problemstellung

Beim produktiven Einsatz von KI-Coding-Assistenten in Entwicklungsteams entstehen erhebliche Herausforderungen:
1. **Vendor Lock-in & Kosten:** Direkte Anbindung an einen einzelnen LLM-Provider führt zu hohen Kosten bei Standardaufgaben und Abhängigkeit von dessen Verfügbarkeit.
2. **Datenschutz (DSGVO / PII):** Quellcode, Kundendaten und vertrauliche Bezeichner werden unmaskiert an Drittanbieter-APIs gesendet.
3. **Mangelnde Qualitätsprüfung & Sicherheit:** Generierter Code wird ungeprüft zurückgegeben, ohne automatische Ausführung in einer sicheren Testumgebung.

**Ziel von miniSashimi:** Ein transparenter, OpenAI-kompatibler Reverse-Proxy (`/v1/chat/completions`), der sich nahtlos vor jeden beliebigen Coding-Agenten schaltet, Anfragen anonymisiert, komplexe Aufgaben zerlegt, kosten- und leistungseffizient auf verschiedene LLMs delegiert, Ergebnisse in einer Docker-Sandbox validiert und Entwicklern über ein Web-Dashboard volle Transparenz und Notfallkontrolle (Kill-Switch) gibt.

---

## 2. Entscheidungstreiber & Qualitätsziele

* **Drop-In-Kompatibilität:** Vollständige Unterstützung der OpenAI API v1 (keine Änderungen an bestehenden Clients/Tools nötig).
* **DSGVO & Datenschutz:** Automatische Erkennung und Maskierung personenbezogener Daten (Boundary Anonymization) vor dem API-Aufruf.
* **Intelligentes LLM-Routing:** Verteilung von Aufgaben an spezialisierte Modelle (z. B. DeepSeek-Coder für Code, OpenAI für High-Level-Reasoning, Qwen für Dokumentation).
* **Isolierte Verifikation:** Automatischer Testlauf des generierten Codes in einer gehärteten Docker-Sandbox.
* **Hexagonale Architektur (Ports & Adapters):** Strikte Trennung von Orchestrierungslogik, Frameworks und Provider-Adaptern.
* **Human Oversight:** Live-Dashboard mit Token- und Kostenradar, Workflow-Graph und manuellem Kill-Switch.

---

## 3. Betrachtete Alternativen & Trade-offs

| Option | Bewertung | Grund für die Ablehnung / Wahl |
| :--- | :--- | :--- |
| **Monolithischer Prompt-Wrapper** | Schneller gebaut, aber starre Bindung an einen Provider und keine modulare Testbarkeit. | Verwurfen zugunsten einer Hexagonalen Gateway-Architektur. |
| **Reines Client-Plugin (IDE Extension)** | Bindet Logik an eine spezifische IDE (z. B. nur VS Code) und schließt Terminal-Tools oder CLI-Agenten aus. | Verwurfen – Ein lokaler Proxy bedient alle IDEs und CLI-Agenten universell. |
| **Hexagonaler Reverse-Proxy mit Multi-Worker Pool (Gewählt)** | Universelle Kompatibilität, klare Trennung von Domain und Adaptern, flexible Provider-Erweiterung. | **Angenommen:** Maximale Interoperabilität, Flexibilität und Unternehmens-Sicherheit. |

---

## 4. Architekturentscheidung

miniSashimi ist nach dem Muster der **Hexagonalen Architektur (Ports & Adapters)** strukturiert.

```mermaid
flowchart TD
    Client[Coding Agent / IDE / CLI Client] -->|OpenAI v1 Chat API| API_ADAPTER[Inbound Adapter: FastAPI Router]
    
    subgraph miniSashimi Core
        API_ADAPTER --> MID_ANON[Anonymisierungs-Middleware (DSGVO)]
        MID_ANON --> DOMAIN[Domain Service: Conductor & Orchestrator]
        
        DOMAIN --> ROUTER[LLM Routing Engine]
        DOMAIN --> VERIFY[Verifier-Schwarm]
    end

    subgraph Outbound Adapters
        ROUTER --> LLM_POOL[LLM Clients:\nOpenAI | DeepSeek | Qwen | GLM]
        VERIFY --> SANDBOX[Docker Sandbox Adapter\nnetwork=none, read_only]
        DOMAIN --> STORAGE[State & Run Storage: SQLite / RAM]
    end

    subgraph Governance & UI
        DOMAIN <-->|SSE / WebSockets| DASH_ADAPTER[Inbound Adapter: Dashboard Server]
        DASH_ADAPTER <--> DASHBOARD[Browser Dashboard\nReact + Vite + Kill-Switch]
    end
```

### 4.1 Technologie-Stack
* **Backend:** Python 3.11+, FastAPI, Pydantic v2, `uvicorn`.
* **Frontend:** React 18+, Vite, Tailwind CSS, Lucide Icons.
* **Sandbox:** Docker Engine SDK (`backend.Dockerfile`, `sandbox.Dockerfile`).
* **Datenschutz:** Reguläre Ausdrücke & Entitätserkennung mit In-Memory Token Vault.
* **Kommunikation:** Server-Sent Events (SSE) und WebSockets für Echtzeit-Telemetrie.

### 4.2 Kernkomponenten & Schichtenarchitektur

1. **Domain Layer (`app/domain/`):**
   - Reines Python ohne Framework-Abhängigkeiten.
   - Enthält Kernmodelle (`Workflow`, `Task`, `AgentState`) und Services für Routing- und State-Maschinen.

2. **Ports Layer (`app/ports/`):**
   - Definiert Interfaces für Treiber (Inbound: `API`, `Dashboard`) und getriebene Adapter (Outbound: `LLMClient`, `SandboxExecutor`, `StateRepository`).

3. **Adapters Layer (`app/adapters/`):**
   - `inbound/api`: OpenAI-kompatibler Endpunkt (`/v1/chat/completions`).
   - `outbound/llm`: Wrapper für OpenAI, DeepSeek, Qwen und GLM mit einheitlichem Protokoll.
   - `outbound/sandbox`: Startet isolierte Docker-Container für die Code-Verifikation.

4. **Middleware (`app/middleware/`):**
   - `anonymizer.py`: Ersetzt sensible Daten durch kryptografische Pseudonyme und stellt sie bei der Antwort wieder her.
   - `rate_limit.py`: Schutz vor API-Spam und Budget-Überschreitungen.

5. **Dashboard (`dashboard/`):**
   - Visualisiert den Workflow in Echtzeit.
   - Bietet einen globalen **Kill-Switch**, der laufende Prozesse und Docker-Container sofort stoppt.

---

## 5. Sicherheit & Compliance

* **Sandbox-Isolation:** Container laufen mit `network_mode=none`, `read_only=true`, `cap_drop=ALL` und Nicht-Root-Benutzer (`user=1000:1000`).
* **DSGVO / PII:** Klientendaten verlassen das System nur pseudonymisiert; Mappings verbleiben rein flüchtig im RAM.
* **Secret Protection:** Umgebungsvariablen und API-Keys werden über Pydantic Settings geladen und niemals in Client-Responses oder Logs exponiert.

---

## 6. Konsequenzen & Trade-offs

### Positive Auswirkungen
* **Volle Interoperabilität:** Funktioniert nahtlos mit allen bestehenden Tools (Cursor, Aider, PI Agent, Continue.dev).
* **Kostenoptimierung:** Aufgaben werden automatisch dem günstigsten Modell zugewiesen, das für die Aufgabe ausreicht.
* **Sichere Code-Ausführung:** Code wird vor der Rückgabe in einer Sandbox getestet, wodurch fehlerhafter Code frühzeitig abgefangen wird.

### Risiken & Gegenmaßnahmen
* **Zusätzliche Latenz durch Middleware & Sandbox:**  
  *Gegenmaßnahme:* Asynchrone Parallelverarbeitung und optionales Streaming bei einfachen Abfragen ohne Code-Ausführung.

---

## 7. Verwandte Dokumente
* [`ARCHITECTURE.md`](./ARCHITECTURE.md) – Detaillierte Architekturspezifikation & API-Kontrakte
* [`Konzept.md`](./Konzept.md) – Fachliches Konzept & Routing-Strategie
* [`README.md`](./README.md) – Setup & Bedienungsanleitung
* [`SECURITY.md`](./SECURITY.md) – Sicherheitsrichtlinien & Härtung
