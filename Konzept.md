Idee:
Ein Orchestrator den ich via openai api (local ansprechen kann und der meinen input in PI Coding Agent in Tasks aufteilt und je nach task an andere llms weiter gibt. Ähnlich zu Sakana Fugu (siehe hintergrund.md)

Phase 1: Analyse & Strategie (Die Planungs-Ebene)

    1. Task: Systemarchitektur & Ablauf-Planung (Thinker)

        Beschreibung: Zerlegt die Benutzeranfrage in granulare Unteraufgaben (Subtasks), entwirft die Modul- und Ordnerstruktur und definiert saubere API-Schnittstellen.  

        Ideales Modell: openai/gpt-5.5-thinking oder anthropic/claude-opus-4.8.  

        Alternative (Z.ai / China): z-ai/glm-5.2 (im Max Thinking-Modus für extrem tiefe, logische Abstraktionsketten über Stunden hinweg).  

    2. Task: Tool-Auswahl & Workflow-Konstruktion (Conductor)

        Beschreibung: Das Herzstück deines lokalen Skripts. Gleicht den Plan mit deinem lokalen RAG (Tool-Datenbank) ab, teilt den Modellen ihre Rollen sowie isolierten Kontexte (Access Lists) zu und erzwingt das exakte JSON-Format zur Ausführung.  

        Ideales Modell: mistralai/mistral-small-2603 (Mistral Small 4).  

        Grund: Es bietet native "Best-in-Class" Agenten-Fähigkeiten, ein 256k-Kontextfenster und höchste JSON-Strukturtreue, um den Orchestration Collapse (Gruppendenken) im Workflow effektiv zu verhindern.  

Phase 2: Code-Synthese & Erstellung (Die Worker-Ebene)

    3. Task: Low-Level Codegenerierung (Worker - Backend/Core)

        Beschreibung: Schreibt den funktionalen Programmiercode (z. B. asynchrone Algorithmen, Datenbank-Anbindungen oder Kern-Logiken) basierend auf dem isolierten Task-Briefing.  

        Ideales Modell (China): deepseek/deepseek-v4-pro.  

        Grund: Die DeepSeek-Reihe bietet die ungeschlagene Benchmark-Führung auf der LiveCodeBench für reine Code-Synthese bei minimalen API-Kosten.  

    4. Task: Visuelle UI/UX-Gestaltung (Worker - Frontend)

        Beschreibung: Übersetzt funktionale Anforderungen in das optische Gerüst einer Browser-Oberfläche (HTML/CSS, Tailwind-Klassen, responsive Raster und Flexbox-Layouts).  

        Ideales Modell (China): qwen/qwen-3-coder-next (oder das dedizierte qwen-3.6-27b).  

        Grund: Alibabas Top-Coder besitzt das präziseste asiatische Verständnis für responsive Frontend-Komponenten und visuelle UI-Hierarchien.  

    5. Task: Test-Driven-Development (Test Architect)

        Beschreibung: Schreibt parallel zur Generierung vollautomatische Unit-Tests (z. B. mit pytest oder Jest), basierend auf den in Phase 1 definierten Anforderungen.  

        Ideales Modell (China): deepseek/deepseek-v4-pro.  

        Grund: Generiert extrem schnell und fehlerfrei syntaktisch korrekte Testabdeckungen und simuliert komplexe Edge-Case-Szenarien.  

    6. Task: Integration & API-Kopplung (Worker - Integration)

        Beschreibung: Verbindet die generierten Einzelmodule der Backend- und Frontend-Worker und schreibt die Logik für deine externen Schnittstellen (wie OpenRouter und dein Terminal-Skript).  

        Ideales Modell (China): minimax/minimax-m3 oder moonshot/kimi-k2.6.  

        Grund: Extreme Pfad-Stabilität und Ausdauer bei sehr langen, autonomen Agentenläufen (Multi-Agent Swarms) über viele aufeinanderfolgende Tool-Calls hinweg.  

Phase 3: Qualitätssicherung & Verifikation (Die Review-Ebene)

    7. Task: Syntax-Validierung & Vorab-Prüfung (Pre-Verifier)

        Beschreibung: Schnelle Prüfung auf einfache Syntaxfehler, Klammerfehler oder JSON-Integrität, bevor teurere Verifikationsschleifen gestartet werden.  

        Ideales Modell (Europa): mistralai/ministral-8b-2512 oder mistralai/ministral-14b-2512.  

        Grund: Diese kleinen europäischen Modelle sind blitzschnell, extrem günstig und filtern formale Flüchtigkeitsfehler kosteneffizient heraus.  

    8. Task: Der Verifier-Schwarm (Tiefenanalyse in 3 Dimensionen)

        8.1 Security Auditor: Scannt den Code ausschließlich auf Sicherheitslücken (SQLi, XSS, unverschlüsselte Keys).  

            Modell: z-ai/glm-5.2 (im High Thinking-Modus).  

        8.2 Performance Scout: Analysiert Algorithmen auf Zeit- und Speicherkomplexität (O(n)) und optimiert asynchrone Schleifen.  

            Modell: openai/gpt-5.5-thinking.  

        8.3 Dependency Watchdog: Gleicht Importe ab und sorgt dafür, dass keine veralteten (deprecated) oder unsicheren Bibliotheken verwendet werden.  

            Modell: google/gemini-3.1-pro.  

    9. Task: Optische Qualitätsprüfung (Visual Verifier)

        Beschreibung: Lädt das gerenderte Ergebnis der Browser-Oberfläche (via automatisiertem Screenshot aus einer Headless-Browser-Sandbox wie Playwright) und prüft das Design visuell auf Layout-Verschiebungen.  

        Ideales Modell (US): google/gemini-3.1-pro oder openai/gpt-5.5-pro.  

        Grund: Die führenden US-Modelle bieten dank hochentwickelter Vision-Fähigkeiten die beste Pixel-Genauigkeit, um optische Fehler im Screenshot sofort zu erkennen und CSS-Korrekturanweisungen auszugeben.  

Phase 4: Ausführung & Evolution (Die Laufzeit-Ebene)

    10. Task: Lokale Sandbox-Ausführung (Runtime Executor)

        Beschreibung: Führt den generierten Code in einer isolierten Docker-Umgebung oder einem unprivilegierten Container aus und fängt stdout, stderr sowie Exit-Codes ab.  

        Ideales Modell: mistralai/mistral-small-2603.  

        Grund: Verarbeitet CLI-Befehle exzellent und interpretiert Fehlermeldungen direkt, um bei einem Absturz den autonomen "Self-Healing"-Loop anzustoßen.  

    11. Task: Git- & Workspace-Management (Repository Janitor)

        Beschreibung: Analysiert bestehende Git-Repositories, erstellt Feature-Branches, löst einfache Merge-Konflikte und generiert saubere, semantische Commit-Nachrichten.  

        Ideales Modell (Europa): mistralai/ministral-14b-2512.  

        Grund: Klein, extrem schnell bei Git-Diff-Analysen und hochgradig ressourcensparend für repetitive Datei-Operationen.
