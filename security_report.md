# Security Review & Fix Report

- **Status:** Erfolgreich bereinigt
- **Iterationen:** 6 von 6

## Historie der Iterationen

* **Iteration 1**: 2 Befunde gefunden (0 Kritisch, 2 Warnung) -> 2 behoben. (F-001: Boundary Placeholder Collision, F-002: WebSocket Race Condition)
* **Iteration 2**: 3 Befunde gefunden (1 Kritisch, 2 Optimierung) -> 3 behoben. (F-003: Rate Limiting Memory Exhaustion, F-004: Directory Permissions, F-005: NameError Crash)
* **Iteration 3**: 0 Befunde gefunden (0 Kritisch, 0 Warnung) -> Qualitäts-Gate bestanden.
* **Iteration 4**: 6 Befunde gefunden (0 Kritisch, 4 Warnung, 2 Optimierung) -> 6 behoben. (F-006: Validation Handler Leak, F-007: Streaming exception leak, F-008: XFF IP-Spoofing, F-009: Unenforced Cost Limit, F-010: WebSocket Client Registry Leak, F-011: Cleartext Token Cache Storage)
* **Iteration 5**: 3 Befunde gefunden (0 Kritisch, 1 Warnung, 2 Optimierung) -> 3 behoben. (F-012: SSRF via base URLs, F-013: Anonymizer Cache Limit, F-014: Monolithic main.py)
* **Iteration 6**: 2 Befunde gefunden (0 Kritisch, 2 Warnung) -> 2 behoben. (F-015: Vulnerable dependencies in requirements.txt - bleach and starlette)

## Details der Behebungen (Fixes)

| Datei | Zeile | Schwachstelle (CWE) | Beschreibung des Fixes |
| :--- | :--- | :--- | :--- |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | 41-53 | [CWE-209] | Request-Body aus der JSONResponse des `validation_exception_handler` entfernt; unzensierten Log-Eintrag durch `sanitize_value` ersetzt. |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | 119-147 | [CWE-312] | Client-IDs (Tokens/IPs) im Rate-Limiter (`check_rate_limit`) mit SHA-256 gehasht, um Plaintext-Storage im Memory-Cache zu vermeiden. |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | 151-164 | [CWE-290] | Der Header `X-Forwarded-For` wird im Rate-Limiter nur ausgewertet, wenn die Umgebungsvariable `SASHIMI_TRUST_PROXY=true` gesetzt ist. |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | 710-727 | [CWE-209] | Streaming-Generator liefert bei Exceptions eine generische Fehlermeldung an den Client aus; die detailreichen Fehler-Traces werden nur serverseitig protokolliert. |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | 820-867 | [CWE-400] | WebSocket-Lifespan vollständig in `try ... finally` umschlossen, um die Bereinigung getrennter WebSocket-Verbindungen bei abnormalem Beenden abzusichern. |
| [app/orchestrator/engine.py](file:///var/home/peppi/coding/miniSashimi/app/orchestrator/engine.py) | 299-302 | [CWE-400] | Aufruf von `self.check_cost_limit()` am Ende der `add_cost()` Methode eingefügt, um Kostenüberschreitungen aktiv bei Token-Zuweisungen abzufangen. |
| [tests/test_security.py](file:///var/home/peppi/coding/miniSashimi/tests/test_security.py) | 80-105 | [CWE-400] | Unit-Test `test_cost_limit_enforcement` zur Simulation und Validierung der Kostenlimit-Überschreitung hinzugefügt. |
| [app/core/security.py](file:///var/home/peppi/coding/miniSashimi/app/core/security.py) | 38-68 | [CWE-918] | Einführung der `is_safe_url` Hilfsfunktion zur Filterung/Prüfung von URLs auf private IP-Bereiche und DNS-Auflösung (SSRF-Schutz). |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | 935-940 | [CWE-918] | Validierung eingehender base_url Konfigurationen gegen `is_safe_url` bei Einstellungs-Updates. |
| [app/adapters/outbound/llm/client.py](file:///var/home/peppi/coding/miniSashimi/app/adapters/outbound/llm/client.py) | 131-135 | [CWE-918] | Überprüfung ausgehender base_url Pfade gegen `is_safe_url` vor Aufbau der HTTP-Verbindung. |
| [app/middleware/anonymizer.py](file:///var/home/peppi/coding/miniSashimi/app/middleware/anonymizer.py) | 38-43 | [CWE-400] | Einführung einer Kapazitätsgrenze (max 5.000 Einträge) im Anonymizer-Cache zur Vermeidung von Speichererschöpfung (DoS). |
| [app/core/security.py](file:///var/home/peppi/coding/miniSashimi/app/core/security.py) & [app/core/database.py](file:///var/home/peppi/coding/miniSashimi/app/core/database.py) | - | - | Isolation von DB-Dateien und Keys/Salts basierend auf `SASHIMI_ENV` für getrennte Umgebungen (Dev/Test-Agenten-Isolation). |
| [app/main.py](file:///var/home/peppi/coding/miniSashimi/app/main.py) | - | [SOLID] | Vollständige Aufspaltung der main.py in Router [rest.py](file:///var/home/peppi/coding/miniSashimi/app/adapters/inbound/rest.py) und [websocket.py](file:///var/home/peppi/coding/miniSashimi/app/adapters/inbound/websocket.py), Bündelung der Middlewares, Re-Export für Test-Kompatibilität. |
| [tests/test_security.py](file:///var/home/peppi/coding/miniSashimi/tests/test_security.py) | 105-138 | [CWE-400] | Hinzufügen von `test_anonymizer_capacity_limit` und `test_ssrf_url_validation` zur Validierung der neuen Schutzmechanismen. |
| [requirements.txt](file:///home/peppi/coding/miniSashimi/requirements.txt) | - | [CWE-1104] | Upgrade von bleach auf 6.4.0 und starlette auf 1.3.1 via pip-compile --upgrade zur Behebung bekannter Sicherheitslücken (F-015). |

## Verbliebene Risiken & Hinweise
* **Ignorierte Meldungen (Low/Info/Optimierung):**
  - Keine verblieben. Alle erkannten Optimierungspunkte (einschließlich WebSocket Client Registry Leak, In-Memory Plaintext Token Cache Storage, Anonymizer Cache Limit und Monolithische main.py) wurden vollständig behoben.
* **Nicht behebbare Fehler:**
  - Keine verblieben. Alle Unit- und Integrationstests wurden mit Exit Code 0 erfolgreich durchlaufen.
