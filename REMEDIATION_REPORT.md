# CodeUltra Remediation Report — miniSashimi

## Status

- **Loop-Status:** ERFOLGREICH BEHOBEN
- **Review-Tier:** Full, Brownfield
- **Offene kritische Befunde:** 0
- **Offene Warnungen:** 0
- **Verbleibende Optimierungen:** 1 (Starlette-TestClient-Deprecation-Warning)

## Behobene Befunde

1. HumanEval-Codeausführung akzeptiert ausschließlich explizit konfigurierte Bubblewrap-Sandboxes mit Netzwerk-, User-, PID-, IPC- und UTS-Isolation, tmpfs-Root, read-only Runtime-Mounts und isoliertem Arbeitsverzeichnis.
2. Chat-, DPO- und allgemeine HTTP-Payloads besitzen aggregierte Größenlimits; Workflows, DPO-Jobs und Quantisierungsprozesse sind gleichzeitig, zeitlich und hinsichtlich Speicherplatz begrenzt.
3. Lokale SSRF-Prüfungen lehnen unspecified, reserved, multicast und link-local Ziele ab.
4. GitHub-Actions sind auf immutable Commit-SHAs gepinnt; CI verwendet `python -m pytest` und minimale Repository-Berechtigungen.
5. Deterministische Architektur-Ratchets und `AGENTS.md` dokumentieren die Modulgrenzen.
6. Sicherheitsberichte wurden an den geprüften Stand angeglichen. Generierte Chat-Antworten tragen ein maschinenlesbares Transparenzsignal.

## Sensor-Evidence

- `python -m pytest -q`: **77 passed** (ein nicht sicherheitsrelevanter Starlette-TestClient-Deprecation-Hinweis)
- `tests/run_tests.py`: **bestanden**
- `dashboard`: Vitest, oxlint und Vite-Build **bestanden**
- `pip-audit -r requirements.txt`: **keine bekannten Schwachstellen**
- `git diff --check`: **bestanden**
